#include <Arduino.h>
#include <NimBLEDevice.h>
#include "baseline_bytes.h"
#include <atomic>
#include <nimble/nimble/host/include/host/ble_gatt.h>
#include "bench_control.h"

// Temporary USB-operated diagnostic. Never linked into firmware/heltec.
static constexpr uint8_t UUID[12]={0x2c,0,0x4c,0,9,0x51,0x34,0x30,0x37,0x35,0x37,0x39};
static const char* VERSION="bench-control-0.3.0-draft";
struct Chunk {size_t len;uint32_t at;uint8_t bytes[520];};
QueueHandle_t rx;
std::atomic<bool> overflow(false);
std::atomic<uint32_t> rxBytes(0),rxMaxChunk(0);
NimBLEClient* client=nullptr;
NimBLERemoteCharacteristic* writer=nullptr;
vesc::Parser parser;
bench::Input input;
bench::Controller control;
bool motorMatch=false,appMatch=false,motorSeen=false,appSeen=false,pending=false;
uint8_t motorCapture[482]{},appCapture[494]{};uint8_t dumpKind=0;size_t dumpOffset=0;
uint8_t requested=0,awaitSamples=0;
uint16_t lastReplyBytes=0;uint8_t lastReplyId=255;
std::atomic<bool> writeBusy(false),writeFailed(false);
uint32_t positiveRequests=0,zeroRequests=0;
int32_t lastCurrentMa=-1;uint32_t activeStarted=0;
uint32_t writeAt=0,leaseAt=0;bool leaseReady=false;uint8_t releaseStep=0;
uint32_t queryAt=0,lastDrive=0,lastScan=0;
char output[900];size_t outputLen=0,outputAt=0;

void emit(const char* line){if(outputAt<outputLen)return;outputLen=snprintf(output,sizeof(output),"%s\n",line);if(outputLen>=sizeof(output))outputLen=0;outputAt=0;}
void pump(){if(outputAt>=outputLen)return;int n=Serial.availableForWrite();if(n>0)outputAt+=Serial.write((const uint8_t*)output+outputAt,min(size_t(n),outputLen-outputAt));}
void event(const char* text){char b[180];snprintf(b,sizeof(b),"{\"event\":\"%s\"}",text);emit(b);}
void notified(NimBLERemoteCharacteristic*,uint8_t* bytes,size_t n,bool){
    rxBytes+=n;rxMaxChunk=std::max(uint32_t(n),rxMaxChunk.load());
    if(n>520){overflow=true;return;}Chunk c{};c.len=n;c.at=millis();memcpy(c.bytes,bytes,n);if(xQueueSend(rx,&c,0)!=pdTRUE)overflow=true;
}
int written(uint16_t,const ble_gatt_error* error,ble_gatt_attr*,void*){
    if(error->status)writeFailed=true;writeBusy=false;return 0;
}
bool send(const uint8_t* p,size_t n){
    if(!writer || !client->isConnected() || writeBusy)return false;
#if !BENCH_ENABLE_MOTOR_COMMANDS
    if(n>2 && (p[2]==6 || p[2]==63))return false;
#endif
    // NimBLE's synchronous writeValue waits indefinitely. Use its underlying
    // asynchronous acknowledged-write API so USB abort/deadlines remain serviced.
    writeAt=millis();writeBusy=true;
    int rc=ble_gattc_write_flat(client->getConnHandle(),writer->getHandle(),p,n,written,nullptr);
    if(rc){writeBusy=false;writeFailed=true;return false;}
    if(n==10 && p[2]==6){if(vesc::i32(p+3)>0)positiveRequests++;else zeroRequests++;}
    return true;
}
void release(){
    if(!control.releasePending || writeBusy)return;
    leaseReady=false;
    // Zero torque then relinquish ADC inhibition, one acknowledged write at a time.
    if(input.identity && client->isConnected()){
        uint8_t b[16];
        if(releaseStep==0){size_t n=bench::current(false,b);if(send(b,n))releaseStep=1;return;}
        if(releaseStep==1){size_t n=bench::lease(false,b);if(send(b,n))releaseStep=2;return;}
    }
    control.releasePending=false;releaseStep=0;
}
void transportStatus(){
    char b[350];snprintf(b,sizeof(b),"{\"type\":\"transport\",\"connected\":%s,\"rx_bytes\":%lu,\"max_chunk\":%lu,\"parser_bytes\":%u,\"last_reply_id\":%u,\"last_reply_bytes\":%u,\"query\":%u,\"gatt_busy\":%s}",client->isConnected()?"true":"false",(unsigned long)rxBytes.load(),(unsigned long)rxMaxChunk.load(),unsigned(parser.used),lastReplyId,lastReplyBytes,requested,writeBusy?"true":"false");emit(b);
}
void status(){
    char b[880];snprintf(b,sizeof(b),
      "{\"type\":\"bench_status\",\"firmware\":\"%s\",\"motor_commands_compiled\":%s,\"release_pending\":%s,\"positive_requests\":%lu,\"zero_requests\":%lu,\"uptime_ms\":%lu,\"state\":%u,\"reason\":\"%s\",\"identity\":%s,\"baseline_match\":%s,\"values_valid\":%s,\"adc_valid\":%s,\"values_age_ms\":%lu,\"adc_age_ms\":%lu,\"erpm\":%ld,\"motor_a\":%.3f,\"volts\":%.2f,\"fault\":%u,\"throttle\":%.5f,\"throttle_v\":%.5f,\"requested_ma\":%ld,\"pulse_elapsed_ms\":%lu}",
      VERSION,BENCH_ENABLE_MOTOR_COMMANDS?"true":"false",control.releasePending?"true":"false",(unsigned long)positiveRequests,(unsigned long)zeroRequests,(unsigned long)millis(),unsigned(control.state),control.reason,input.identity?"true":"false",input.baseline?"true":"false",input.haveValues?"true":"false",input.haveAdc?"true":"false",(unsigned long)(millis()-input.valuesAt),(unsigned long)(millis()-input.adcAt),(long)input.values.erpm,input.values.motorA,input.values.voltage,input.values.fault,input.throttle,input.throttleV,(long)control.requestedMa,control.state==bench::State::Running?(unsigned long)(millis()-control.startedAt):0UL);emit(b);
}
void command(const String& line){
    if(line=="keepalive"){input.hostAt=millis();return;}
    if(line=="abort"){control.stop("operator_abort");return;}
    if(line=="status"){status();return;}
    if(line=="dump motor" && motorSeen){dumpKind=1;dumpOffset=0;return;}
    if(line=="dump app" && appSeen){dumpKind=2;dumpOffset=0;return;}
    if(line=="preflight" && control.state==bench::State::Idle){
        input.baseline=false;motorMatch=appMatch=motorSeen=appSeen=false;pending=false;parser.reset();xQueueReset(rx);event("preflight_requested");return;
    }
    if(line=="arm wheels-up-neutral"){
#if !BENCH_ENABLE_MOTOR_COMMANDS
        event("read_only_build_cannot_arm");return;
#endif
        input.hostAt=millis();input.usb=bool(Serial);
        if(!control.arm(millis(),input))event("arm_denied_check_status");return;
    }
    if(line=="dry-run" || line=="probe" || line=="run positive-forward-confirmed"){
        bool dry=line=="dry-run";uint32_t ms=line=="probe"?250:5000;
        if(!control.start(millis(),input,ms,dry))event("start_denied_check_status");return;
    }
    event("unknown_command");
}
void serialInput(){
    static char line[80];static size_t n=0;static bool discarded=false;
    for(int count=0;count<100 && Serial.available();count++){
        char c=Serial.read();if(c=='\r')continue;
        if(c=='\n'){if(!discarded){line[n]=0;command(String(line));}n=0;discarded=false;}
        else if(n<sizeof(line)-1)line[n++]=c;else discarded=true;
    }
}
void clearLink(){
    control.stop("ble_disconnected");control.releasePending=false;
    input.connected=input.identity=input.baseline=input.haveValues=input.haveAdc=false;
    motorMatch=appMatch=motorSeen=appSeen=pending=false;writer=nullptr;writeBusy=false;leaseReady=false;releaseStep=0;parser.reset();xQueueReset(rx);
}
void connectReadOnly(){
    if(millis()-lastScan<5000)return;lastScan=millis();
    auto scan=NimBLEDevice::getScan();auto results=scan->getResults(1500,false);
    for(int j=0;j<results.getCount();j++){
        auto d=results.getDevice(j);
        if(d->getName()!="VESC BLE UART" || d->getAddress().toString()!="d8:94:35:be:e5:32")continue;
        if(!client->connect(d))continue;
        auto svc=client->getService("6e400001-b5a3-f393-e0a9-e50e24dcca9e");
        auto w=svc?svc->getCharacteristic("6e400002-b5a3-f393-e0a9-e50e24dcca9e"):nullptr;
        auto n=svc?svc->getCharacteristic("6e400003-b5a3-f393-e0a9-e50e24dcca9e"):nullptr;
        if(!w||!n||!w->canWrite()||!n->canNotify()||!n->subscribe(true,notified)){client->disconnect();continue;}
        writer=w;input.connected=true;control.reason="read_only_preflight";queryAt=0;break;
    }
    scan->clearResults();
}
void readResponses(){
    if(overflow.exchange(false)){parser.reset();xQueueReset(rx);input.haveValues=input.haveAdc=false;control.stop("rx_queue_overflow");}
    Chunk c;
    for(int count=0;count<24 && xQueueReceive(rx,&c,0)==pdTRUE;count++){
        parser.feed(c.bytes,c.len,[&](const uint8_t* p,size_t n){
            lastReplyId=p[0];lastReplyBytes=n;
            if(!pending)return;
            if(awaitSamples){
                uint8_t bit=p[0]==4?1:p[0]==32?2:0;if(!bit || !(awaitSamples&bit))return;
                awaitSamples &= ~bit;pending=awaitSamples!=0;
            }else{if(p[0]!=requested)return;pending=false;}
            if(p[0]==0){input.identity=vesc::matchesIdentity(p,n,UUID);if(!input.identity)client->disconnect();}
            else if(input.identity && p[0]==14){motorSeen=n==sizeof(motorCapture);if(motorSeen)memcpy(motorCapture,p,n);motorMatch=n==sizeof(MOTOR_BASELINE) && !memcmp(p,MOTOR_BASELINE,n);event(motorMatch?"motor_baseline_matched":"motor_baseline_mismatch");}
            else if(input.identity && p[0]==17){appSeen=n==sizeof(appCapture);if(appSeen)memcpy(appCapture,p,n);appMatch=n==sizeof(APP_BASELINE) && !memcmp(p,APP_BASELINE,n);event(appMatch?"app_baseline_matched":"app_baseline_mismatch");}
            else if(input.identity && vesc::decode(p,n,input.values)){input.haveValues=true;input.valuesAt=c.at;}
            else if(input.identity && bench::adc(p,n,input.throttle,input.throttleV)){input.haveAdc=true;input.adcAt=c.at;}
            if(!input.baseline && motorMatch && appMatch){input.baselineAt=c.at;if(control.state==bench::State::Idle)control.reason="preflight_matched_idle";}
            input.baseline=motorMatch&&appMatch;
        });
    }
}
void poll(){
    if(pending && millis()-queryAt>((requested==14 || requested==17)?2500:500)){pending=false;parser.reset();control.stop("query_timeout");}
    if(pending || writeBusy || leaseReady || control.releasePending || millis()-queryAt<35)return;
    requested=!input.identity?0:!motorSeen?14:!appSeen?17:4;
    uint8_t b[16];awaitSamples=requested==4?3:0;
    size_t n=awaitSamples?bench::sampleQueries(b):bench::query(requested,b);queryAt=millis();pending=true;
    if(!send(b,n))pending=false;
}
void drive(){
    control.tick(millis(),input);
    if(writeFailed.exchange(false) || (writeBusy && millis()-writeAt>1000)){
        control.stop("ble_write_failed_result_unknown");client->disconnect();return;
    }
    release();
    if(control.releasePending || writeBusy || control.state!=bench::State::Running)return;
    if(activeStarted!=control.startedAt){activeStarted=control.startedAt;lastCurrentMa=-1;leaseAt=millis()-300;leaseReady=false;}
    uint8_t b[16];uint32_t now=millis();
    if(leaseReady){
        if(now-leaseAt>200){control.stop("adc_lease_ack_too_late");release();return;}
        leaseReady=false;
    }
    // Renew the finite ADC lease independently. Repeated unchanged current
    // commands previously starved the two telemetry queries on this BLE module.
    if(now-leaseAt>=150){
        size_t n=bench::lease(true,b);if(send(b,n)){leaseReady=true;leaseAt=millis();}return;
    }
    if(control.requestedMa!=lastCurrentMa || now-lastDrive>=400){
        size_t n=bench::current(control.requestedMa!=0,b);
        if(send(b,n)){lastDrive=millis();lastCurrentMa=control.requestedMa;}
    }
}
void dumpNext(){
    if(!dumpKind || outputAt<outputLen)return;
    auto data=dumpKind==1?motorCapture:appCapture;size_t length=dumpKind==1?sizeof(motorCapture):sizeof(appCapture);
    size_t count=min(size_t(40),length-dumpOffset);char hex[81],b[220];
    for(size_t i=0;i<count;i++)snprintf(hex+2*i,3,"%02x",data[dumpOffset+i]);
    snprintf(b,sizeof(b),"{\"type\":\"config_dump\",\"name\":\"%s\",\"offset\":%u,\"total\":%u,\"hex\":\"%s\"}",dumpKind==1?"motor":"app",unsigned(dumpOffset),unsigned(length),hex);emit(b);
    dumpOffset+=count;if(dumpOffset==length)dumpKind=0;
}
void diagnosticStep();
void diagnosticWorker(void*){
    NimBLEDevice::init("GoKart Isolated Bench");NimBLEDevice::setPower(3);
    auto scan=NimBLEDevice::getScan();scan->setActiveScan(true);scan->setInterval(100);scan->setWindow(50);
    client=NimBLEDevice::createClient();client->setConnectTimeout(3000);
    for(;;)diagnosticStep();
}
void setup(){
    Serial.begin(115200);Serial.setTxTimeoutMs(0);rx=xQueueCreate(24,sizeof(Chunk));
    // Keep the radio amplifier and GPS off in this diagnostic; no LoRa commands.
    pinMode(7,OUTPUT);digitalWrite(7,LOW);pinMode(34,OUTPUT);digitalWrite(34,HIGH);
    event("diagnostic_boot_read_only_disarmed");
    xTaskCreatePinnedToCore(diagnosticWorker,"bench-diagnostic",16384,nullptr,1,nullptr,0);
}
void loop(){delay(100);}
void diagnosticStep(){
    input.usb=bool(Serial);serialInput();
    input.connected=client->isConnected();
    if(!input.connected){clearLink();connectReadOnly();}
    else {readResponses();drive();poll();}
    // Any disconnected state has no command replay; finite VESC ADC lease expires.
    dumpNext();static uint32_t reported=0;static int reports=0;if(!dumpKind && millis()-reported>=100){reported=millis();if(++reports%10==0)transportStatus();else status();}
    pump();delay(2);
}
