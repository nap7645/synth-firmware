# synth-firmware

Firmware for Eurorack synthesizer modules.

## Modules

### Euclid
Euclidean rhythm generator / master clock.

- `euclid/euclid1/` — single-bank prototype. STM32G474RE (Nucleo-64).
  Three patterns (K1/K2/K3) sharing one length N, plus SUM, SOC and clock outputs.

## Toolchain
STM32CubeIDE + STM32Cube HAL. Board: NUCLEO-G474RE, HSI+PLL @ 170 MHz.
