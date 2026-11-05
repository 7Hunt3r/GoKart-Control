#include <GoKartProtocol.h>
#include <cstdio>
int main(){
    GkTelemetry t{};gkFillHeader(t.h,GK_MSG_TELEMETRY,1,GK_ADDR_ALL,17);
    t.lat_e7=410000000;t.lon_e7=-730000000;t.gps_kph_x10=36;t.heading_x10=900;
    t.sats=7;t.flags=GK_FLAG_GPS_FIX|GK_FLAG_VESC_LINK;t.volts_x10=762;
    t.motor_a_x10=1400;t.batt_a_x10=-105;t.erpm=-3000;t.duty_pct=4;t.esc_temp_x10=242;
    t.mode_req=GK_MODE_UNKNOWN;t.mode_actual=GK_MODE_UNKNOWN;t.scale_x1000=65535;t.gps_age_s=1;
    for(size_t i=0;i<sizeof(t);i++)printf("%02x",reinterpret_cast<const unsigned char*>(&t)[i]);
    puts("");
}
