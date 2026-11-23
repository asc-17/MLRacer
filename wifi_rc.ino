// Wi-Fi RC test: drive the car from a laptop (or phone) browser, no extra libraries
//
// The ESP32 makes its own Wi-Fi network. On the laptop:
//   1. Connect to Wi-Fi  "AIML-Racer"   password  "racer1234"
//   2. Open  http://192.168.4.1  in Chrome / Edge
//   3. Drive with W A S D or the arrow keys (hold two keys for curves), Space = stop,
//      1..9 = 10..90 % speed, 0 = 100 %. The on-screen buttons work too (also on a phone).
//
// Hand-gesture driving: run the laptop app in gesture_control/ (see its README). It uses the same
// /cmd commands as this page, so this one sketch serves both - whichever you open drives the car.
// Use one at a time: an open, idle page only reads the battery (/status) and never sends commands.
//
// Power: battery switch ON and the c6 -> V5 wire plugged IN (USB unplugged).
//        To upload new code: switch OFF, pull the c6 -> V5 wire, plug USB, upload, then reverse.
// Safety: if the laptop stops sending for 0.5 s (out of range, tab closed) the car stops.
//
// HTTP:  /cmd?c=<F B L R G I H J S>&v=<10..100>   drive (repeat at least every 0.5 s)
//        /status                                 battery only, doesn't touch the motors
//        both reply  {"vbat":7.92,"pct":73,"usb":false,"cmd":"F"}

#include <WiFi.h>
#include <WebServer.h>

const char* WIFI_NAME = "AIML-Racer";
const char* WIFI_PASS = "racer1234";       // at least 8 characters

// ---------------- Pins ----------------
// Front = the end that drives forward on W. Channel A (G25 / G26 / G27) is the LEFT motor, channel B the RIGHT.
// (Swapped from before so forward / back are the right way round while turns stay correct.)
const int L_PWM = 25, L_IN1 = 26, L_IN2 = 27;   // left motor,  TB6612 channel A (AO1 / AO2)
const int R_PWM = 33, R_IN1 = 22, R_IN2 = 13;   // right motor, TB6612 channel B (BO1 / BO2)
const int STBY = 4;
const int VBAT_PIN = 32;                        // 100k / 39k divider
const int LED_PIN = 2;

// If a wheel turns the wrong way when you press forward, set its line to true.
const bool INVERT_LEFT  = true;    // channel A motor runs the other way round
const bool INVERT_RIGHT = false;

const float VBAT_DIVIDER = (100.0 + 39.0) / 39.0;
const float TURN = 0.7;          // spin-in-place speed (fraction of the chosen speed)
const float CURVE_INNER = 0.4;   // inner wheel speed on curves
const unsigned long FAILSAFE_MS = 500;
const int PWM_FREQ = 20000, PWM_BITS = 8;

// Battery: 6 x AA NiMH (Eneloop). Resting pack voltage -> charge %. NiMH discharges very flat in
// the middle, so treat the % as an estimate. Motors sag the voltage, so the % only updates after
// they have been off for REST_MS (the voltage shown is always live).
const float BAT_V[]   = {6.00, 6.60, 7.08, 7.20, 7.38, 7.56, 7.80, 8.10, 8.40};
const int   BAT_PCT[] = {   0,    5,   15,   20,   30,   50,   70,   90,  100};
const unsigned long REST_MS = 2000;

WebServer server(80);
char cmd = 'S';
float speed = 1.0;               // 0..1, set by the slider / number keys (1.0 = full battery voltage)
unsigned long lastCmdTime = 0;
float vbat = 0;                  // live, smoothed (sags while driving)
float vbatRest = 0;              // smoothed, motors-off readings only
int batPct = 0;
unsigned long lastDriveTime = 0; // last time the motors were on (0 = not since power-up)

// ---------------- Motors ----------------
void motor(int pwm, int in1, int in2, float s) {   // s = -1..+1
  s = constrain(s, -1.0f, 1.0f);
  digitalWrite(in1, s > 0);
  digitalWrite(in2, s < 0);
  ledcWrite(pwm, (int)(fabs(s) * 255));
}

void drive(float left, float right) {
  motor(L_PWM, L_IN1, L_IN2, INVERT_LEFT  ? -left  : left);
  motor(R_PWM, R_IN1, R_IN2, INVERT_RIGHT ? -right : right);
}

void apply(char c) {
  float s = speed, t = speed * CURVE_INNER;
  switch (c) {
    case 'F': drive( s,  s); break;
    case 'B': drive(-s, -s); break;
    case 'L': drive(-s * TURN,  s * TURN); break;   // spin left
    case 'R': drive( s * TURN, -s * TURN); break;   // spin right
    case 'G': drive( t,  s); break;                 // forward-left
    case 'I': drive( s,  t); break;                 // forward-right
    case 'H': drive(-t, -s); break;                 // back-left
    case 'J': drive(-s, -t); break;                 // back-right
    case 'a': drive( s,  0); break;                 // motor test: left forward
    case 'z': drive(-s,  0); break;                 //             left backward
    case 'k': drive( 0,  s); break;                 //             right forward
    case 'm': drive( 0, -s); break;                 //             right backward
    default:  drive( 0,  0); c = 'S'; break;
  }
  cmd = c;
}

int batteryPercent(float v) {
  const int n = sizeof(BAT_V) / sizeof(BAT_V[0]);
  if (v <= BAT_V[0]) return 0;
  for (int i = 1; i < n; i++)
    if (v < BAT_V[i])
      return BAT_PCT[i - 1] + (int)((v - BAT_V[i - 1]) / (BAT_V[i] - BAT_V[i - 1]) * (BAT_PCT[i] - BAT_PCT[i - 1]) + 0.5f);
  return 100;
}

void readBattery() {
  float v = analogReadMilliVolts(VBAT_PIN) / 1000.0 * VBAT_DIVIDER;
  vbat = (vbat < 1) ? v : vbat * 0.9 + v * 0.1;     // smooth it
  if (cmd == 'S' && (lastDriveTime == 0 || millis() - lastDriveTime > REST_MS)) {
    vbatRest = (vbatRest < 1) ? v : vbatRest * 0.9 + v * 0.1;
    batPct = batteryPercent(vbatRest);
  }
}

String statusJson() {                               // reply for /cmd and /status
  char buf[80];
  if (vbat < 1) snprintf(buf, sizeof(buf), "{\"vbat\":0,\"pct\":null,\"usb\":true,\"cmd\":\"%c\"}", cmd);
  else snprintf(buf, sizeof(buf), "{\"vbat\":%.2f,\"pct\":%d,\"usb\":false,\"cmd\":\"%c\"}", vbat, batPct, cmd);
  return String(buf);
}

// ---------------- Web page ----------------
const char PAGE[] = R"rawliteral(<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>AIML Racer</title>
<style>
 body{margin:0;font-family:system-ui,sans-serif;background:#111;color:#eee;display:flex;flex-direction:column;align-items:center;user-select:none;-webkit-user-select:none}
 h1{font-size:22px;margin:14px 0 4px}
 #bar{display:flex;gap:18px;font-size:15px;margin-bottom:10px}
 #link.ok{color:#4caf50} #link.bad{color:#f44336}
 #batbox{display:inline-block;position:relative;width:30px;height:13px;border:2px solid #bbb;border-radius:3px;vertical-align:-3px;margin:0 5px 0 3px}
 #batbox:after{content:'';position:absolute;right:-6px;top:3px;width:3px;height:7px;background:#bbb;border-radius:0 2px 2px 0}
 #batfill{position:absolute;left:1px;top:1px;bottom:1px;width:0;background:#4caf50;border-radius:1px}
 #bat{color:#999} .low{color:#f44336}
 .pad{display:grid;grid-template-columns:repeat(3,90px);grid-template-rows:repeat(3,90px);gap:8px}
 button{font-size:26px;border:none;border-radius:14px;background:#2a2a2a;color:#eee;touch-action:none}
 button.on{background:#ff6d00;color:#000}
 #stop{background:#b71c1c}
 .row{display:flex;gap:8px;margin-top:8px}
 .row button{font-size:14px;width:104px;height:48px}
 h2{font-size:14px;color:#999;margin:18px 0 0;font-weight:normal}
 #spd{width:290px;margin-top:14px}
 #help{font-size:13px;color:#888;margin:14px;text-align:center;line-height:1.6}
</style></head><body>
<h1>AIML Racer</h1>
<div id="bar"><span id="link" class="bad">connecting...</span><span>battery<span id="batbox"><i id="batfill"></i></span><b id="pct">-</b> <small id="bat"></small></span><span>cmd <b id="cmd">S</b></span></div>
<div class="pad">
 <button data-c="G">&#8598;</button><button data-c="F">&#8593;</button><button data-c="I">&#8599;</button>
 <button data-c="L">&#8634;</button><button id="stop" data-c="S">&#9632;</button><button data-c="R">&#8635;</button>
 <button data-c="H">&#8601;</button><button data-c="B">&#8595;</button><button data-c="J">&#8600;</button>
</div>
<label>speed <b id="spdv">100</b> %<br><input id="spd" type="range" min="10" max="100" step="10" value="100"></label>
<h2>single-motor test (hold)</h2>
<div class="row"><button data-c="a">LEFT fwd</button><button data-c="z">LEFT back</button></div>
<div class="row"><button data-c="k">RIGHT fwd</button><button data-c="m">RIGHT back</button></div>
<div id="help">W A S D or arrow keys &middot; hold two keys to curve &middot; Space = stop<br>1-9 = 10-90 % speed &middot; 0 = 100 %</div>
<script>
let cur='S', btnCmd=null, speed=100, busy=false, dirty=false, last=0;
const keys=new Set(), $=id=>document.getElementById(id);
function show(t){
  let j; try{j=JSON.parse(t)}catch(e){$('pct').textContent=t;return}
  if(j.cmd)$('cmd').textContent=j.cmd;
  if(j.usb){$('pct').textContent='USB';$('bat').textContent='';$('batfill').style.width='0';return}
  const p=Math.max(0,Math.min(100,j.pct));
  $('pct').textContent=p+' %'; $('pct').className=p<15?'low':'';
  $('bat').textContent=j.vbat.toFixed(2)+' V';
  $('batfill').style.width=Math.round(p*0.24)+'px';
  $('batfill').style.background=p<15?'#f44336':p<35?'#ffa000':'#4caf50';
}
function req(url){
  busy=true; dirty=false;
  fetch(url,{cache:'no-store'})
   .then(r=>r.text()).then(t=>{show(t);$('link').textContent='connected';$('link').className='ok'})
   .catch(()=>{$('link').textContent='NO LINK';$('link').className='bad'})
   .finally(()=>{busy=false;last=Date.now();if(dirty)send()});
}
function send(){
  if(busy){dirty=true;return}
  req('/cmd?c='+cur+'&v='+speed);
}
function set(c){
  if(c===cur)return; cur=c; $('cmd').textContent=c;
  document.querySelectorAll('button').forEach(b=>b.classList.toggle('on',b.dataset.c===c&&c!=='S'));
  send();
}
function fromKeys(){
  const f=keys.has('f'),b=keys.has('b'),l=keys.has('l'),r=keys.has('r');
  const L=l&&!r, R=r&&!l;
  if(f&&!b) return L?'G':R?'I':'F';
  if(b&&!f) return L?'H':R?'J':'B';
  return L?'L':R?'R':'S';
}
function update(){ set(btnCmd || fromKeys()) }
const KEYMAP={w:'f',arrowup:'f',s:'b',arrowdown:'b',a:'l',arrowleft:'l',d:'r',arrowright:'r'};
function setSpeed(v){speed=v;$('spd').value=v;$('spdv').textContent=v;if(cur!=='S')send()}
addEventListener('keydown',e=>{
  const k=e.key.toLowerCase();
  if(KEYMAP[k]){keys.add(KEYMAP[k]);e.preventDefault();update()}
  else if(k===' '){keys.clear();btnCmd=null;e.preventDefault();update()}
  else if(k>='0'&&k<='9'){setSpeed(k==='0'?100:+k*10)}
});
addEventListener('keyup',e=>{const k=KEYMAP[e.key.toLowerCase()]; if(k){keys.delete(k);update()}});
addEventListener('blur',()=>{keys.clear();btnCmd=null;update()});
document.querySelectorAll('button').forEach(b=>{
  b.addEventListener('pointerdown',e=>{b.setPointerCapture(e.pointerId);btnCmd=b.dataset.c==='S'?null:b.dataset.c;if(!btnCmd)keys.clear();update()});
  const up=()=>{btnCmd=null;update()};
  b.addEventListener('pointerup',up); b.addEventListener('pointercancel',up);
});
$('spd').addEventListener('input',e=>setSpeed(+e.target.value));
// keep sending while driving (the car stops by itself if this goes quiet). When stopped, only read the
// battery: /status doesn't touch the motors, so an idle open page never interrupts the gesture app.
setInterval(()=>{ if(cur!=='S') send(); else if(!busy&&Date.now()-last>1000) req('/status') },120);
</script></body></html>)rawliteral";

void handleCmd() {
  if (server.hasArg("v")) speed = constrain(server.arg("v").toInt(), 10, 100) / 100.0;
  String c = server.arg("c");
  apply(c.length() ? c[0] : 'S');
  lastCmdTime = millis();
  server.send(200, "application/json", statusJson());
}

void handleStatus() {                               // read-only: no motor change, no failsafe reset
  server.send(200, "application/json", statusJson());
}

void setup() {
  Serial.begin(115200);
  pinMode(L_IN1, OUTPUT); pinMode(L_IN2, OUTPUT);
  pinMode(R_IN1, OUTPUT); pinMode(R_IN2, OUTPUT);
  pinMode(STBY, OUTPUT); digitalWrite(STBY, HIGH);
  pinMode(LED_PIN, OUTPUT);
  ledcAttach(L_PWM, PWM_FREQ, PWM_BITS);
  ledcAttach(R_PWM, PWM_FREQ, PWM_BITS);
  drive(0, 0);
  readBattery();

  WiFi.mode(WIFI_AP);
  WiFi.softAP(WIFI_NAME, WIFI_PASS);
  WiFi.setSleep(false);                       // lower lag
  WiFi.setTxPower(WIFI_POWER_8_5dBm);         // smaller current bursts (stops brownout resets), still ~10 m range
  server.on("/", [] { server.send(200, "text/html", PAGE); });
  server.on("/cmd", handleCmd);
  server.on("/status", handleStatus);
  server.begin();

  Serial.printf("\nWi-Fi \"%s\" (password %s) is up. Open http://%s\n",
                WIFI_NAME, WIFI_PASS, WiFi.softAPIP().toString().c_str());
}

void loop() {
  server.handleClient();
  if (cmd != 'S') lastDriveTime = millis();         // battery % waits until the motors have rested

  if (cmd != 'S' && millis() - lastCmdTime > FAILSAFE_MS) {
    apply('S');
    Serial.println("failsafe: no commands, stopped");
  }

  static unsigned long lastBat = 0;
  if (millis() - lastBat > 200) { lastBat = millis(); readBattery(); }

  // LED: solid while driving, slow blink while waiting
  digitalWrite(LED_PIN, cmd != 'S' ? HIGH : (millis() / 500) % 2);
}
