# Bullen – 6-kanalig telefonrouter för Raspberry Pi 5 + Audio Injector Octo

MVP som uppfyller:
- Inspelning per kanal (WAV, pre-gain/mute)
- Autostart via systemd
- VU-meter (peak/RMS) i UI
- Gain/mute per kanal
- Routing: välj 1 av 6 kanaler till headset L/R med snabb växling

## Arkitektur
- Ljudmotor: `app/engine/audio_engine.py` (JACK/PipeWire-JACK)
- API/WS + UI: FastAPI + WebSocket, UI i `app/ui/index.html`
- Entrypoint: `Bullen.py` (startar Uvicorn och appen `app.server.main:app`)
- Konfiguration: `config.yaml`

## Krav på Raspberry Pi 5
- HAT: Audio Injector Octo aktiverad
- Ljudstack: PipeWire med JACK-API (Bookworm standard)
- Paket:
  ```bash
  sudo apt update
  sudo apt install -y pipewire pipewire-audio pipewire-jack wireplumber alsa-utils libsndfile1
  # Valfritt för diagnos: qpwgraph/pw-top
  sudo apt install -y qjackctl qpwgraph
  ```
- Python-deps:
  ```bash
  sudo apt install -y python3-pip python3-venv
  cd /home/pi/Bullen
  python3 -m venv .venv
  source .venv/bin/activate
  pip install --upgrade pip
  pip install -r requirements.txt
  ```

## Aktivera HAT
Redigera `/boot/firmware/config.txt` (Bookworm) och lägg till:
```
dtoverlay=audioinjector-octo
# Rekommenderas att stänga av intern audio
# dtparam=audio=off
```
Reboota och verifiera:
```bash
arecord -l
aplay -l
```
Du ska se ett kort med 6 in och 8 ut.

## Kör lokalt (test)
```bash
export BULLEN_CONFIG=/home/pi/Bullen/config.yaml
python3 Bullen.py
# Öppna i webbläsare: http://<Pi-IP>:8000/  (omdirigeras till /ui)
```

## systemd-autostart
1) Uppdatera sökvägar i `systemd/bullen.service` om din projektmapp skiljer sig.
2) Installera tjänsten:
```bash
sudo cp systemd/bullen.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable bullen.service
sudo systemctl start bullen.service
sudo systemctl status bullen.service
```

## Rättigheter (realtid)
Aktivera RT-prioritet och memlock:
```
# /etc/security/limits.d/audio.conf
@audio - rtprio 95
@audio - memlock unlimited
```
Lägg användaren i gruppen `audio`:
```bash
sudo usermod -aG audio $USER
```
Logga ut/in.

## Konfiguration
`config.yaml` (exempelvärden finns):
```yaml
samplerate: 48000
frames_per_period: 128
nperiods: 2
inputs: 6
outputs: 2
record: true
recordings_dir: recordings
auto_connect_capture: true
auto_connect_playback: true
capture_match: capture
playback_match: playback
selected_channel: 1
backend: jack  # 'jack' (full funktion) eller 'dummy' (ingen JACK; UI-only)
```
- `capture_match`/`playback_match` används för att auto-ansluta fysiska portar (PipeWire/JACK). Justera vid behov.

### Backend-val (JACK vs. Dummy)
- `backend: jack` (standard) använder JACK/PipeWire-JACK och ger full funktion:
  - 6 in → välj 1 till L/R, gain/mute per kanal, VU (peak/RMS), inspelning per kanal.
- `backend: dummy` kör utan JACK-server (ingen audio I/O, ingen inspelning):
  - UI och API fungerar, VU visar 0, kontroller uppdaterar intern state men påverkar inget ljud.

Sätt backend på ett av tre sätt:
- I `config.yaml`: `backend: dummy`
- Miljövariabel: `export BULLEN_BACKEND=dummy`
- Installationsskript-flagga: `./scripts/install_and_start.sh --backend=dummy`

## UI
- Öppna `http://<Pi-IP>:8000/` => redirect till `/ui/`
- Välj kanal (CH1–CH6) för monitor i headset L/R
- Mute/Gain per kanal
- VU-meter visar RMS med peak-markör

## Inspelningar
- Skapas i `recordings/<timestamp>/channel_<N>.wav`
- Inspelning sker pre-gain/mute för att undvika destruktiva ändringar. Kan ändras i motorn om behövs.

## Tips för latens
- RPi 5 klarar ofta 48 kHz, 128 frames, 2 perioder (~5.3 ms). Trimma i PipeWire/JACK om XRUNs uppstår.

## Känd begränsning (MVP)
- Ingen DSP (AGC/limiter/AEC) i MVP (kan läggas till fas 2)
- Endast en vald kanal till L/R; ingen mix av flera kanaler
- UI är minimalistiskt
- I `dummy`-backend finns ingen audio I/O och ingen inspelning (avsiktligt för miljöer utan JACK)

## Felsökning
- Visa porter och kopplingar: `pw-top`, `qpwgraph`
- Se loggar: `journalctl -u bullen.service -f`
- Fel "JACK library not available": säkerställ `pipewire-jack` är installerat och aktivt.
