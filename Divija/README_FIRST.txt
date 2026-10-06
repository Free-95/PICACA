PICACA FINAL STAGE-1 MODEL - HARDWARE HANDOFF
Date: 6 October 2026

RUN THIS ONE FILE
-----------------
PICACA_STAGE1_FINAL.py

This is the final revised Stage-1-only chain:

    B1-prime physics and direction check
      -> B2-prime learned dynamics
      -> V3 command context
      -> B3-prime safety-preserving fusion
      -> B4-prime adaptive projection
      -> ALLOW / DELAY_ALERT / BLOCK_ALARM

It is not the later P1/P3/P4 generalized model. It contains no P3 or P4 code.
All frozen configuration, B2 envelopes and V3 calibration tables are embedded.
It does not require any CSV, JSON or model-parameter file.

REQUIREMENTS
------------
- Python 3.10 or newer.
- No Python packages are required; it uses only the standard library.
- A 1 Hz input stream is expected.

FIRST RUN
---------
Open a terminal in this folder and run:

    python PICACA_STAGE1_FINAL.py

This runs the internal self-test and prints one demonstration decision. The
self-test must end with:

    SELF_TEST=PASS

MODBUS TCP RUN
--------------
The runtime implements the exact register map in the supplied
"Modbus TCP Register Map.pdf". Run it as a safety proxy in front of the ESP32:

    python PICACA_STAGE1_FINAL.py --modbus-proxy 192.168.1.50

Replace 192.168.1.50 with the ESP32's address. Then point the HMI/command client
to the computer running PICACA, TCP port 1502. PICACA reads the physical state
continuously and intercepts actuator-coil writes before forwarding them.

Optional settings:

    --port 502 --unit 1 --interval 1.0
    --listen-host 0.0.0.0 --listen-port 1502

Hardware register map, using the human-readable one-based addresses from the PDF:

    COILS (FC01 read, FC05/FC15 write)
    00001  P101_CMD: 0=OFF, 1=ON
    00002  MV101_CMD: 0=CLOSE, 1=OPEN

    DISCRETE INPUTS (FC02 read)
    10001  P101_ACTUAL_STATE: 0=OFF, 1=ON
    10002  FLOAT_SWITCH_SAFE: 0=TRIPPED, 1=SAFE

    INPUT REGISTERS (FC04 read)
    30001  LIT101_LEVEL: 0..1000; 855 means 85.5 percent
    30002  FIT101_FLOW: value divided by 10; 25 means 2.5 m3/h

    HOLDING REGISTERS (passed through unchanged)
    40001  FAULT_MODE: 0=normal, 1=frozen, 2=stale, 3=jump
    40002  FAULT_VALUE: 0..1000

The PDF's printed addresses are encoded as zero-based PDU offsets: for example,
30001 is sent as offset 0 with function code 04. The runtime already handles
this; do not add another offset.

There are no PICACA decision holding registers in the supplied map. The proxy enforces
the decision directly:

    ALLOW       forward the original coil write to the ESP32
    DELAY_ALERT do not forward; return Modbus exception 06 (device busy)
    BLOCK_ALARM do not forward; return Modbus exception 04 (device failure)

Every monitor and command decision is also printed as one JSON line for logging.
The proxy fails closed if sensor data is missing/stale, during the first 14
one-second warm-up samples, or when FLOAT_SWITCH_SAFE is 0.

IMPORTANT CONTROL RULE
----------------------
The HMI must connect to PICACA's proxy port, not directly to the ESP32. Otherwise
the coil write reaches the actuator before PICACA can authorize it. The ESP32
remains on its normal port 502; PICACA listens for the HMI on port 1502 by
default. Start PICACA and wait at least 14 seconds before issuing a command.

JSON-LINE MODE
--------------
For testing without Modbus:

    python PICACA_STAGE1_FINAL.py --stdin-json

Send one JSON object per line, for example:

{"timestamp_s":1,"lit101_mm":500,"fit101_m3_h":2.5,"mv101_state":"OPEN","p101_on":1,"p102_on":0,"proposed_command":"NO_COMMAND"}

The model writes one JSON decision per line. Run at least 14 continuous 1 Hz
NO_COMMAND samples before testing a command so the rolling history is ready.

VERIFICATION COMPLETED BEFORE HANDOFF
-------------------------------------
- Python syntax/compile check: PASS.
- Four built-in hazardous/protective tests: PASS.
- B1-prime synthetic comparison: 362/362 cases matched.
- Full official Stage-1 attack recording: 449,919/449,919 B1, B3 and B4
  decisions matched the frozen revised branch, including all 740 command rows.
- Safety invariants B3 >= B1 and B4 >= B3 are checked at runtime.
- Hardware-map FC01/FC02/FC04/FC05 protocol test: PASS.
- Pre-execution proxy authorization (hazardous block, protective allow and
  tripped-float fail-closed): PASS.

There were four tiny B1 physics-risk numeric differences on monitoring rows
immediately after the known 82-second source-data gap. The live runtime resets
history after a timing gap instead of projecting across missing samples. None of
the 449,919 decisions changed.

LIMITS
------
- This is research prototype software, not a certified PLC safety function.
- LIT101 uses the supplied 0..1000 percent-x10 value directly as the project's frozen
  compatibility input; internally level_percent = input / 10. The
  20/25/80/90 boundaries are project policy thresholds.
- The script runs on a laptop, Raspberry Pi or edge computer and communicates
  with the ESP32/PLC over Modbus TCP. It is not MicroPython/Arduino firmware.
- P102 is absent from the supplied map and is fixed OFF in this Stage-1 adapter.
- Verify the real rig's byte order, network path, scale factors and
  exception handling before operating actuators.

FROZEN CONFIGURATION
--------------------
revised_config_frozen_v1.json file SHA-256 embedded in the runtime:
8DA6CCD79979F5DEA4D6C043A0FC4F6DE2A22D21E12A3898964B382B222483C5

The configuration's internal historical `_self_sha256` field is:
CB09C07D28DCC66A95741FEBEC558D489CBF3B20DB06B2258110C3AEF3B4C338
