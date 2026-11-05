#include <Arduino.h>
#include <NimBLEDevice.h>
#include <RadioLib.h>
#include <TinyGPSPlus.h>
#include <ArduinoJson.h>
#include <WiFi.h>
#include <ArduinoOTA.h>
#include <Preferences.h>
#include <atomic>
#include "vesc_protocol.h"
#include "secrets.h"
#include "wifi.local.h"

// Exact HTIT-WB32LAF V4.3 schematic, not the V4.2 GC1109 mapping.
static constexpr int RADIO_CS=8,RADIO_DIO1=14,RADIO_RST=12,RADIO_BUSY=13,RADIO_SCK=9,RADIO_MISO=11,RADIO_MOSI=10;
static constexpr int FEM_POWER=7,FEM_CSD=2,FEM_CTX=5;
static constexpr uint8_t EXPECTED_UUID[12]={0x2c,0,0x4c,0,9,0x51,0x34,0x30,0x37,0x35,0x37,0x39};
static const char* SERVICE="6e400001-b5a3-f393-e0a9-e50e24dcca9e";
static const char* NUS_RX="6e400002-b5a3-f393-e0a9-e50e24dcca9e";
static const char* NUS_TX="6e400003-b5a3-f393-e0a9-e50e24dcca9e";
static const char* VERSION="0.1.3-bench";
static constexpr uint32_t STALE_MS=2000;
static constexpr uint32_t OTA_WINDOW_MS=600000;

SPIClass radioSPI(FSPI);
SX1262 radio=new Module(RADIO_CS,RADIO_DIO1,RADIO_RST,RADIO_BUSY,radioSPI);
TinyGPSPlus gps;
Preferences prefs;
struct Chunk {uint16_t len;uint8_t data[256];};
struct Snapshot {vesc::Values values;uint32_t sampled=0;uint32_t samples=0;uint32_t responseMs=0;bool verified=false;};
struct Event {char text[180];};
QueueHandle_t rxQueue,events;
portMUX_TYPE stateMux=portMUX_INITIALIZER_UNLOCKED;
Snapshot current;
std::atomic<bool> maintenance(false),blePaused(false),bleConnected(false),rxOverflow(false);
std::atomic<uint32_t> rxDrops(0);
std::atomic<bool> radioDone(false);
bool radioReady=false,radioActive=false,txEnabled=false,otaActive=false;
uint32_t bootId,sequence=0,lastTx=0,txStarted=0,txCount=0,txErrors=0,otaStarted=0;
int radioInit=-999;
bool otaRebootPending=false;uint32_t otaRebootAt=0;
uint8_t kartId=0; // bench/uncommissioned; do not assign this baseline kart fleet ID 1 implicitly.
String hostName,apName;
char serialOutput[1536];size_t serialLength=0,serialOffset=0;
void emitLine(const String& line){
    if(serialOffset<serialLength || line.length()+1>=sizeof(serialOutput))return;
    memcpy(serialOutput,line.c_str(),line.length());serialOutput[line.length()]='\n';
    serialLength=line.length()+1;serialOffset=0;
}
void pumpOutput(){
    if(serialOffset>=serialLength)return;
    int room=Serial.availableForWrite();if(room<=0)return;
    size_t count=min(size_t(room),serialLength-serialOffset);
    serialOffset+=Serial.write((const uint8_t*)serialOutput+serialOffset,count);
}

void event(const char* text){Event e{};snprintf(e.text,sizeof(e.text),"%s",text);xQueueSend(events,&e,0);}
Snapshot snapshot(){portENTER_CRITICAL(&stateMux);Snapshot s=current;portEXIT_CRITICAL(&stateMux);return s;}
void notifyCB(NimBLERemoteCharacteristic*,uint8_t* data,size_t n,bool){
    // Queued to the BLE worker; no parsing or serial/radio waits in BLE callbacks.
    if(n>256){rxOverflow=true;rxDrops++;return;}
    Chunk c{};c.len=n;memcpy(c.data,data,n);
    if(xQueueSend(rxQueue,&c,0)!=pdTRUE){rxOverflow=true;rxDrops++;}
}
void bleWorker(void*){
    NimBLEDevice::init("GoKart Control Bench");
    NimBLEDevice::setPower(3);
    auto scan=NimBLEDevice::getScan();scan->setActiveScan(true);scan->setInterval(100);scan->setWindow(50);
    auto client=NimBLEDevice::createClient();client->setConnectTimeout(4000);
    NimBLERemoteCharacteristic* writer=nullptr;
    vesc::Parser parser;
    bool identified=false,requestPending=false;
    uint32_t lastQuery=0,lastRx=0,lastScan=0,lastRejectReset=0;
    std::vector<std::string> rejected;std::string candidateAddress;
    for(;;){
        if(maintenance){
            if(client->isConnected()) client->disconnect();
            writer=nullptr;identified=false;requestPending=false;bleConnected=false;
            portENTER_CRITICAL(&stateMux);current.verified=false;portEXIT_CRITICAL(&stateMux);
            blePaused=true;vTaskDelay(pdMS_TO_TICKS(20));continue;
        }
        blePaused=false;
        if(!client->isConnected()){
            bleConnected=false;writer=nullptr;identified=false;requestPending=false;parser.reset();
            portENTER_CRITICAL(&stateMux);current.verified=false;portEXIT_CRITICAL(&stateMux);
            xQueueReset(rxQueue);
            if(millis()-lastScan<5000){vTaskDelay(pdMS_TO_TICKS(20));continue;}
            lastScan=millis();event("ble_scan");
            if(millis()-lastRejectReset>60000){rejected.clear();lastRejectReset=millis();}
            auto results=scan->getResults(2000,false);
            for(int i=0;i<results.getCount() && !maintenance;i++){
                auto device=results.getDevice(i);
                if(device->getName()!="VESC BLE UART") continue; // NUS alone is also used by unrelated sensors.
                char line[180];snprintf(line,sizeof(line),"ble_candidate address=%s name=%s rssi=%d",device->getAddress().toString().c_str(),device->getName().c_str(),device->getRSSI());event(line);
                candidateAddress=device->getAddress().toString();
                if(std::find(rejected.begin(),rejected.end(),candidateAddress)!=rejected.end())continue;
                if(!client->connect(device)) continue;
                auto svc=client->getService(SERVICE);
                auto w=svc?svc->getCharacteristic(NUS_RX):nullptr;
                auto n=svc?svc->getCharacteristic(NUS_TX):nullptr;
                if(!w || !n || !(w->canWrite()||w->canWriteNoResponse()) || !n->canNotify() || !n->subscribe(true,notifyCB)){
                    client->disconnect();event("ble_nus_unavailable");continue;
                }
                writer=w;bleConnected=true;lastQuery=0;lastRx=millis();event("ble_connected_identity_pending");break;
            }
            scan->clearResults();
        }
        if(writer && client->isConnected()){
            if(rxOverflow.exchange(false)){parser.reset();xQueueReset(rxQueue);event("ble_rx_overflow_resync");}
            Chunk c;
            while(xQueueReceive(rxQueue,&c,0)==pdTRUE){
                parser.feed(c.data,c.len,[&](const uint8_t* p,size_t n){
                    if(p[0]==0){
                        identified=vesc::matchesIdentity(p,n,EXPECTED_UUID);
                        portENTER_CRITICAL(&stateMux);current.verified=identified;portEXIT_CRITICAL(&stateMux);
                        event(identified?"vesc_identity_verified_fw_6_00":"vesc_identity_rejected");
                        if(!identified){if(rejected.size()<16)rejected.push_back(candidateAddress);client->disconnect();}
                    }
                    vesc::Values v;
                    if(identified && vesc::decode(p,n,v)){
                        portENTER_CRITICAL(&stateMux);
                        current.values=v;current.sampled=millis();current.samples++;current.responseMs=millis()-lastQuery;
                        portEXIT_CRITICAL(&stateMux);
                    }
                    lastRx=millis();requestPending=false;
                });
            }
            if(requestPending && millis()-lastQuery>1200){parser.reset();requestPending=false;event("vesc_response_timeout");}
            if(client->isConnected() && millis()-lastRx>6000){client->disconnect();event("ble_stale_disconnect_read_only");}
            if(client->isConnected() && !requestPending && millis()-lastQuery>=250 && !maintenance){
                uint8_t frame[6];vesc::query(identified?4:0,frame);
                lastQuery=millis();requestPending=true;
                // This BLE characteristic write carries only allowlisted GET requests.
                if(!writer->writeValue(frame,sizeof(frame),writer->canWrite())){requestPending=false;client->disconnect();}
            }
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

void initRadio(){
    pinMode(FEM_POWER,OUTPUT);digitalWrite(FEM_POWER,HIGH);
    pinMode(FEM_CSD,OUTPUT);digitalWrite(FEM_CSD,HIGH);
    pinMode(FEM_CTX,OUTPUT);digitalWrite(FEM_CTX,HIGH);
    radioSPI.begin(RADIO_SCK,RADIO_MISO,RADIO_MOSI,RADIO_CS);
    // Conservative bench setting. SX1262 output is NOT final antenna power (external FEM).
    radioInit=radio.begin(915.0,500.0,7,5,0x12,0,8,1.8,false);
    radioReady=radioInit==RADIOLIB_ERR_NONE;
    if(radioReady){radio.setDio2AsRfSwitch(true);radio.setDio1Action([](){radioDone=true;});}
}
void put16(uint8_t* b,uint16_t v){b[0]=v;b[1]=v>>8;}
void put32(uint8_t* b,uint32_t v){for(int i=0;i<4;i++)b[i]=v>>(8*i);}
uint16_t age16(uint32_t sample,bool exists){return exists?min(uint32_t(65534),uint32_t(millis()-sample)):65535;}
void transmit(){
    if(!txEnabled || maintenance || !radioReady) return;
    if(radioActive){
        if(radioDone.exchange(false)){radio.finishTransmit();radioActive=false;txCount++;}
        else if(millis()-txStarted>1000){radio.standby();radioActive=false;txErrors++;}
        return;
    }
    if(millis()-lastTx<2000)return;
    lastTx=millis();auto s=snapshot();
    bool v=s.verified && s.samples && millis()-s.sampled<STALE_MS;
    bool g=gps.location.isValid() && gps.location.age()<STALE_MS;
    uint8_t b[47]{};b[0]='G';b[1]='K';b[2]=1;b[3]=kartId;
    put32(b+4,bootId);put32(b+8,sequence++);put32(b+12,millis());put16(b+16,age16(s.sampled,s.samples));
    b[18]=(v?1:0)|(g?2:0);b[19]=v?s.values.fault:255;
    if(v){put16(b+20,uint16_t(s.values.voltage*10));put16(b+22,int16_t(constrain(s.values.batteryA*10,-32768.f,32767.f)));put16(b+24,int16_t(constrain(s.values.motorA*10,-32768.f,32767.f)));put32(b+26,s.values.erpm);put16(b+30,int16_t(s.values.escC*10));}
    if(g){put32(b+32,int32_t(gps.location.lat()*1e7));put32(b+36,int32_t(gps.location.lng()*1e7));}
    put16(b+40,gps.speed.isValid() && gps.speed.age()<STALE_MS?uint16_t(min(65534.0,gps.speed.mps()*100)):65535);
    b[42]=gps.satellites.isValid() && gps.satellites.age()<STALE_MS?min(uint32_t(254),gps.satellites.value()):255;
    put16(b+43,gps.hdop.isValid() && gps.hdop.age()<STALE_MS?min(uint32_t(65534),uint32_t(gps.hdop.value())):65535);
    put16(b+45,gps.location.isValid()?min(uint32_t(65534),uint32_t(gps.location.age())):65535);
    radioDone=false;int state=radio.startTransmit(b,sizeof(b));
    if(state==RADIOLIB_ERR_NONE){radioActive=true;txStarted=millis();}else{txErrors++;}
}
void status(){
    auto s=snapshot();bool fresh=s.verified && s.samples && millis()-s.sampled<STALE_MS;
    JsonDocument d;d["type"]="status";d["firmware"]=VERSION;d["boot_id"]=bootId;d["uptime_ms"]=millis();d["kart_id"]=kartId;
    d["vesc_connected"]=bleConnected.load();d["identity_verified"]=s.verified;d["vesc_fresh"]=fresh;
    d["samples"]=s.samples;d["response_ms"]=s.responseMs;d["rx_drops"]=rxDrops.load();d["sample_age_ms"]=nullptr;
    if(s.samples)d["sample_age_ms"]=millis()-s.sampled;
    if(fresh){d["battery_voltage_v"]=s.values.voltage;d["battery_current_a"]=s.values.batteryA;d["motor_current_a"]=s.values.motorA;d["erpm"]=s.values.erpm;d["duty"]=s.values.duty;d["esc_temperature_c"]=s.values.escC;d["fault_code"]=s.values.fault;d["controller_id"]=s.values.controller;}
    else{for(const char* key:{"battery_voltage_v","battery_current_a","motor_current_a","erpm","duty","esc_temperature_c","fault_code","controller_id"})d[key]=nullptr;}
    d["motor_temperature_c"]=nullptr;d["motor_temperature_reason"]="sensor_not_wired";
    d["gps_chars"]=gps.charsProcessed();d["gps_checksum_pass"]=gps.passedChecksum();d["gps_fix"]=gps.location.isValid() && gps.location.age()<STALE_MS;
    if(d["gps_fix"].as<bool>()){d["latitude_deg"]=gps.location.lat();d["longitude_deg"]=gps.location.lng();}else{d["latitude_deg"]=nullptr;d["longitude_deg"]=nullptr;}
    d["satellites"]=gps.satellites.isValid() && gps.satellites.age()<STALE_MS?int(gps.satellites.value()):-1;
    d["radio_init"]=radioInit;d["tx_enabled"]=txEnabled;d["tx_completed"]=txCount;d["tx_errors"]=txErrors;
    d["maintenance"]=maintenance.load();d["ota_active"]=otaActive;d["free_heap"]=ESP.getFreeHeap();
    if(otaActive){d["ota_hostname"]=hostName;d["ota_ip"]=WiFi.localIP().toString();}
    String line;serializeJson(d,line);emitLine(line);
}
void enterMaintenance(){
    auto s=snapshot();
    if(!s.verified || !s.samples || millis()-s.sampled>STALE_MS || s.values.erpm!=0){event("maintenance_denied_need_fresh_stationary_vesc_or_physical_button");return;}
    maintenance=true;event("maintenance_requested");
}
void stopMaintenance(){ArduinoOTA.end();WiFi.disconnect(true);WiFi.mode(WIFI_OFF);otaActive=false;maintenance=false;event("maintenance_closed");}
void serviceMaintenance(){
    static bool joining=false;
    static uint32_t joinStarted=0;
    if(!maintenance){joining=false;return;}
    if(blePaused && !otaActive && !joining){
        if(radioActive){radio.standby();radioActive=false;} txEnabled=false;
        WiFi.mode(WIFI_STA);WiFi.setHostname(hostName.c_str());WiFi.begin(WIFI_SSID,WIFI_PASSWORD);
        joinStarted=millis();joining=true;event("maintenance_joining_wifi");
    }
    if(joining && WiFi.status()==WL_CONNECTED){
        ArduinoOTA.setHostname(hostName.c_str());ArduinoOTA.setPassword(OTA_PASSWORD);ArduinoOTA.setPort(3232);ArduinoOTA.setRebootOnSuccess(false);
        ArduinoOTA.onStart([](){event("ota_started");});
        ArduinoOTA.onError([](ota_error_t error){char b[64];snprintf(b,sizeof(b),"ota_error=%d",int(error));event(b);});
        ArduinoOTA.onEnd([](){event("ota_complete_rebooting_heltec_only");otaRebootPending=true;otaRebootAt=millis()+1500;});
        ArduinoOTA.begin();otaStarted=millis();otaActive=true;joining=false;event("maintenance_open_10_minutes");
    }
    if(joining && millis()-joinStarted>30000){event("maintenance_wifi_timeout");joining=false;stopMaintenance();}
    if(otaRebootPending && int32_t(millis()-otaRebootAt)>=0)ESP.restart();
    if(otaActive){if(!otaRebootPending)ArduinoOTA.handle();if(millis()-otaStarted>OTA_WINDOW_MS)stopMaintenance();}
}
void command(const String& line){
    if(line=="status")status();
    else if(line=="tx on antenna-confirmed" && !maintenance){txEnabled=radioReady;event(txEnabled?"tx_enabled":"tx_failed_radio_unavailable");}
    else if(line=="tx off"){txEnabled=false;event("tx_disabled");}
    else if(line=="maintenance on")enterMaintenance();
    else if(line=="maintenance off")stopMaintenance();
    else if(line=="help")event("status | tx on antenna-confirmed | tx off | maintenance on | maintenance off | id 0..8");
    else if(line.startsWith("id ") && line.length()==4 && line[3]>='0' && line[3]<='8'){
        kartId=line[3]-'0';prefs.putUChar("kart_id",kartId);event("kart_id_saved");
    }else event("command_rejected_no_vesc_write_api");
}
void setup(){
    Serial.begin(115200);Serial.setTxTimeoutMs(0);
    bootId=esp_random();events=xQueueCreate(32,sizeof(Event));rxQueue=xQueueCreate(24,sizeof(Chunk));
    prefs.begin("gkc_bench",false);kartId=prefs.getUChar("kart_id",0);if(kartId>8)kartId=0;
    uint64_t mac=ESP.getEfuseMac();char name[32];snprintf(name,sizeof(name),"gkc-%06lx",(unsigned long)(mac&0xffffff));hostName=name;apName="GoKart-"+hostName;
    pinMode(0,INPUT_PULLUP);
    pinMode(34,OUTPUT);digitalWrite(34,LOW); // GNSS power, active low
    pinMode(42,OUTPUT);digitalWrite(42,HIGH); // GPS reset deasserted
    pinMode(40,OUTPUT);digitalWrite(40,HIGH); // GPS awake
    pinMode(38,INPUT);pinMode(39,INPUT);
    Serial1.begin(9600,SERIAL_8N1,39,-1); // Listen only: GPS TX net is GPIO39; do not drive either UART wire.
    initRadio();
    xTaskCreatePinnedToCore(bleWorker,"vesc-read-only",10000,nullptr,2,nullptr,0);
    event("boot_read_only_tx_off_ota_off");
}
void loop(){
    // Bounded input processing and separate BLE worker: UI/radio waits cannot send motor commands.
    for(int i=0;i<256 && Serial1.available();i++)gps.encode(Serial1.read());
    static char input[96];static size_t used=0;static bool discard=false;
    for(int i=0;i<128 && Serial.available();i++){
        char c=Serial.read();if(c=='\r')continue;
        if(c=='\n'){if(!discard){input[used]=0;command(String(input));}used=0;discard=false;}
        else if(used<sizeof(input)-1) input[used++]=c;else discard=true;
    }
    static uint32_t held=0;static bool buttonHandled=false;
    if(digitalRead(0)==LOW){if(!held)held=millis();if(!buttonHandled && millis()-held>=3000){maintenance=true;buttonHandled=true;event("physical_maintenance_requested_parked_only");}}
    else {held=0;buttonHandled=false;}
    serviceMaintenance();transmit();
    Event e;if(serialOffset>=serialLength && xQueueReceive(events,&e,0)==pdTRUE){JsonDocument d;d["event"]=e.text;d["uptime_ms"]=millis();String line;serializeJson(d,line);emitLine(line);}
    static uint32_t last=0;if(millis()-last>=1000){last=millis();status();}
    pumpOutput();delay(2);
}
