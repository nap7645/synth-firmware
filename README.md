# synth-firmware

Firmware for Eurorack synthesizer modules.

## Modules

### Euclid
Euclidean rhythm generator / master clock.

- `euclid/euclid1/` — single-bank prototype. STM32G474RE (Nucleo-64).
  Three patterns (K1/K2/K3) sharing one length N, plus SUM, SOC and clock outputs.

### DVCO
Digital VCO, basis for Melody Generator. Will be available in Dual/Quad packages.

- `DVCO/DVCO-Firmware-vX/` — Basic shapes; phase (knob), waveshape (knob), freq/FM (knob + VC). Will add more waveshapes, maybe continuous morph

## Toolchain
STM32CubeIDE
