# Euclid 1 — STM32CubeMX Configuration

Board: **NUCLEO-G474RE** (STM32G474RETx, LQFP64). Created from the *Board Selector*, answering
**No** to "initialize all peripherals with their default Mode".

Project: `euclid1`, workspace `~/Desktop/synth-firmware/euclid/`, toolchain **STM32CubeIDE**.

> **STATUS: configuration entered, NOT YET FLASHED.** Every setting below has been entered in
> CubeMX and cross-checked against the schematic and the G474RE datasheet, but nothing has been
> confirmed against running hardware. Work the checklist in section 11 and update this banner.

---

## 1. Final pin map as built

| Pin | Function | Peripheral / mode |
|---|---|---|
| PC0 | K1 gate out | TIM1_CH1 (AF2) |
| PC1 | K2 gate out | TIM1_CH2 (AF2) |
| PC2 | K3 gate out | TIM1_CH3 (AF2) |
| PC3 | SUM gate out | TIM1_CH4 (AF2) |
| PC4 | SOC out | GPIO output, `socOut` |
| PA7 | Clock out | GPIO output, `clkOut` |
| PA0 / PA1 | Tempo encoder A / B | TIM2_CH1 / CH2 (AF1) |
| PA4 / PA6 | N encoder A / B | TIM3_**CH2** / **CH1** (AF2) |
| PA11 / PA12 | K1 encoder A / B | TIM4_CH1 / CH2 (AF10) |
| PB2 / PC12 | K2 encoder A / B | TIM5_CH1 (AF2) / CH2 (AF1) |
| PC6 / PC7 | K3 encoder A / B | TIM8_CH1 / CH2 (AF4) |
| PB0 | Start/Stop button | GPIO input, pull-up, `StartStop` |
| PC5 | Shift button | GPIO input, pull-up, `Shift` |
| PA2 / PA3 | Debug console | LPUART1_TX / RX (routes to ST-Link VCP) |
| PB13 / PB15 | OLED clock / data | SPI2_SCK / SPI2_MOSI |
| PB14 | unused | SPI2_MISO (OLED never replies) |
| PB10 | OLED chip select | GPIO output, `OLED_CS` |
| PB11 | OLED reset | GPIO output, `OLED_RST` |
| PB12 | OLED data/command | GPIO output, `OLED_DC` |
| PA13 / PA14 | Debug | SWDIO / SWCLK |
| PB3 | Debug trace | SWO — see section 9 |
| — | one-shot pulse terminator | TIM17, no pins |

**Note the N encoder.** Its A leg is on PA4, which is TIM3_**CH2**, while every other encoder has
its A leg on CH1. It therefore counts backwards, and the firmware compensates via the `invert`
argument in `ui_init()`. Do not "fix" this in CubeMX.

---

## 2. Clock configuration

These settings are split across two places, which is easy to trip over.

**System Core → RCC** (Mode panel) — only three things matter here:

| Setting | Value |
|---|---|
| High Speed Clock (HSE) | **Disable** |
| Low Speed Clock (LSE) | **Disable** |
| Power Regulator Voltage Scale | **Scale 1 boost** (Parameter Settings) |

Flash Latency shows 4 WS greyed out — it is derived, not set here.

**Clock Configuration tab** (separate top-level tab) — everything else:

| Setting | Value |
|---|---|
| PLL source | HSI (16 MHz) |
| PLLM | /4 → 4 MHz PLL input |
| PLLN | ×85 → 340 MHz VCO |
| PLLP / PLLQ / PLLR | /2 each |
| System Clock Mux | PLLCLK |
| SYSCLK | **170 MHz** |
| AHB prescaler | /1 |
| APB1 / APB2 prescaler | /1 |

**Shortcut:** type `170` into the HCLK (MHz) box and press Enter — CubeMX solves the whole tree.

**The number that actually matters: APB1 timer clocks and APB2 timer clocks must both read
170 MHz.** With prescaler 1699 that gives exactly 100 kHz (170,000,000 ÷ 1700), which is what
`SEQ_TIMER_HZ` in `euclid_config.h` assumes. TIM2/3/4/5 are on APB1, TIM1/8/17 on APB2.

Sanity: the 4 MHz PLL input is inside the G4's 2.66–16 MHz window and the 340 MHz VCO is inside
96–344 MHz. You are near the top of that range because 170 MHz is the part's maximum.

Ignore greyed 170s scattered around USB, ADC, HRTIM and the USARTs — unused peripherals showing
what they would receive. Same for the RTC/LSE branch and the MCO mux.

PC14/PC15 and PF0/PF1 may show RCC oscillator functions despite HSE/LSE being disabled. Those
are Board Selector reservations, harmless, and none of those pins are in this design.

---

## 3. TIM1 — master step clock and four gate outputs

- Clock Source: **Internal Clock**
- Channel1–4: **PWM Generation CH1 / CH2 / CH3 / CH4**

| Field | Value |
|---|---|
| Prescaler | 1699 |
| Counter Mode | Up |
| Counter Period (ARR) | 11539 — placeholder, set at runtime from tempo |
| auto-reload preload | **Enable** |
| Trigger Event Selection TRGO | Update Event |
| Trigger Event Selection TRGO2 | Reset |
| Master/Slave Mode | Disabled |

**Verify the pins resolve to PC0–PC3.** CubeMX will happily offer PA8–PA11 for TIM1 instead.
Click each pin and pick the PCx option explicitly.

TRGO is **not load-bearing** in this firmware — nothing is slaved to TIM1. It's set to Update
Event so a future peripheral could chain off the step clock. TRGO is the `MMS` field in `CR2`;
TRGO2 (`MMS2`) is a second output used mainly for ADC triggering and goes unused.

Two things the firmware does in code that CubeMX cannot express:

- `TIM1->CR1 |= TIM_CR1_URS` so a software-generated update event can't fire the step ISR.
- **Output-compare preload is disabled** on all four channels. `HAL_TIM_PWM_ConfigChannel()`
  enables `OCxPE`, which would make CCR writes take effect one period late — fine on its own,
  but SOC and clock out are GPIO writes that apply immediately, so the two would drift a full
  step apart. Do not re-enable it.

---

## 4. TIM2 / TIM3 / TIM4 / TIM5 / TIM8 — five quadrature encoders

For each: **Combined Channels → Encoder Mode**.

| Timer | CH1 | CH2 | Encoder |
|---|---|---|---|
| TIM2 | PA0 | PA1 | Tempo |
| TIM3 | PA6 | PA4 | N (A leg on CH2 — inverted in firmware) |
| TIM4 | PA11 | PA12 | K1 |
| TIM5 | PB2 | PC12 | K2 |
| TIM8 | PC6 | PC7 | K3 |

Identical parameter settings for all five:

| Field | Value |
|---|---|
| Prescaler | 0 |
| Counter Period (ARR) | **65535** |
| Encoder Mode | **Encoder Mode TI1 and TI2** (×4 decoding) |
| IC1/IC2 Polarity | Rising Edge |
| IC1/IC2 Prescaler | Division 1 |
| **IC1/IC2 Filter** | **8** |
| auto-reload preload | Disable |
| Trigger Event Selection TRGO | Reset |
| Master/Slave Mode | Disabled |

TIM8 is an advanced timer so it also shows TRGO2 — leave at Reset. No NVIC interrupts; the
encoders are polled.

### Pull-ups — the setting that silently breaks everything

**All ten encoder pins need internal pull-ups.** The encoders are 3-pin types with the common
leg grounded, so A and B are switches to GND and float when open. CubeMX defaults
alternate-function pins to *no pull-up and no pull-down*, so this must be set explicitly.

They are **not on the GPIO tab** — that tab lists only pins configured as plain GPIO. Go to
**System Core → GPIO → TIM tab** and set Pull-up on PA0, PA1, PA4, PA6, PA11, PA12, PB2, PC12,
PC6, PC7. Ctrl-select to do all ten at once. Pull resistors still function in AF mode.

Two deliberate departures from the earlier encoder sketches in this repo:

1. **ARR 65535, not 71.** The old code used 71 so one revolution mapped absolutely onto the full
   parameter range (18 PPR × 4 = 72 counts). That breaks here because Tempo and gate length share
   one encoder, so the firmware reads *deltas* and the counter must free-run without wrapping
   early.
2. **Input filter 8.** Hardware debounce for the mechanical contacts. Raise to 15 if you still
   see double-counts; the software fallback is external 10 kΩ pull-ups plus ~100 nF to ground on
   each leg, worth designing into Euclid 3.

---

## 5. TIM17 — one-shot pulse terminator

TIM17 has **no channel and no pins**. It exists only to fire an interrupt that drops the SOC and
clock-out pins at the end of the gate.

- The Mode panel has no clock source dropdown — TIM16/TIM17 are internally clocked only, so it's
  greyed out and that is normal. What you need is the **"Activated" checkbox**.
- Leave the Channel1 dropdown blank.

| Field | Value |
|---|---|
| Prescaler | 1699 |
| Counter Period (ARR) | 11539 — placeholder, rewritten every step |
| One Pulse Mode | **Enable** (the firmware also sets `TIM_CR1_OPM`, so it's fine if absent) |
| auto-reload preload | **Disable** — ARR must take effect immediately |

**Why TIM17 rather than PWM on PA7.** PA7's only free timer channel is TIM17_CH1, because TIM3
is taken by the N encoder, and TIM17 has no slave-mode controller so it can't be hardware-synced
to TIM1. Since PC4 has only `TIM1_ETR` and no compare channel at all, SOC had to be
software-driven regardless. Driving both PA7 and PC4 as plain GPIO makes them bit-for-bit
consistent with each other and removes PA7 from the AF constraint entirely.

---

## 6. GPIO

| Pin | Mode | Pull | Output level | Speed | User Label |
|---|---|---|---|---|---|
| PC4 | Output Push Pull | None | Low | Low | `socOut` |
| PA7 | Output Push Pull | None | Low | Low | `clkOut` |
| PB0 | Input | **Pull-up** | — | — | `StartStop` |
| PC5 | Input | **Pull-up** | — | — | `Shift` |
| PB10 | Output Push Pull | None | **High** | Low | `OLED_CS` |
| PB11 | Output Push Pull | None | **Low** | Low | `OLED_RST` |
| PB12 | Output Push Pull | None | Low | Low | `OLED_DC` |

The rule: **inputs driven by a switch to ground need a pull-up; push-pull outputs need neither.**
A 40 kΩ internal pull is irrelevant next to an output driver of tens of ohms. The exception is
open-drain outputs, which *must* have a pull-up — relevant only if you ever move the OLED to I²C.

**Output level is the initial state**, written inside `MX_GPIO_Init()` before the pin becomes an
output, so it never glitches to the wrong level. It matters for two pins: CS starts **High** so
the display isn't selected during boot, and RST starts **Low** so the panel is held in reset and
stays dark until the driver releases it. DC is don't-care.

Between power-on reset and `MX_GPIO_Init()` all pins float. If the display ever comes up
scrambled at power-up, a 10 kΩ pull-up on CS closes that window.

**User labels must be valid C identifiers** — they become macro names in `main.h`. `Start/Stop`
would generate `#define Start/Stop_Pin` and fail the build. The firmware uses raw port and pin
macros deliberately, so labels are documentation only.

Buttons are **polled, not EXTI** — the two-second both-buttons-held save gesture needs continuous
sampling anyway, so edge interrupts would add a second code path tracking the same state.

Leave PA5 (LD2) unconfigured; the save indicator moved to the display.

---

## 7. LPUART1 — debug console

- Mode: **Asynchronous**, 115200, 8N1, no flow control
- Pins: PA2 (TX), PA3 (RX)

**Not USART2.** The Board Selector pre-assigns PA2/PA3 as `VCP_TX`/`VCP_RX` from the Nucleo board
definition, and **LPUART1 is the only peripheral CubeMX offers on those pins** for this part.
Both USART2 and LPUART1 appear blocked until you assign the pins. Click PA2 → LPUART1_TX and
PA3 → LPUART1_RX; if CubeMX won't override the VCP label, right-click → Reset_State first.

The VCP is just traces from PA2/PA3 to the ST-Link MCU, so whichever peripheral drives those pins
reaches the virtual COM port.

Consequence for the firmware: `app.c` externs **`hlpuart1`**, not `huart2`.

If CubeMX warns about the baud rate, set LPUART1's clock source to **HSI** on the Clock
Configuration tab — 16 MHz divides more cleanly than 170 MHz.

Optional. `EUCLID_DEBUG_UART` defaults to 0 and the firmware runs without it, but the banner it
prints confirms the clock configured, `app_init()` was reached, and flash recall worked, all in
one line.

---

## 8. SPI2 — OLED (Adafruit 326, SSD1306)

- Mode: **Full-Duplex Master**
- Hardware NSS Signal: **Disable**

| Field | Value |
|---|---|
| Prescaler | /16 → ~10.6 MHz |
| Data size | 8 bits |
| First bit | MSB First |
| CPOL / CPHA | Low / 1 Edge (mode 0) |

Full-duplex rather than Transmit Only even though the OLED never replies, so the same bus stays
usable for daughter MCUs later. PB14 (MISO) goes unconnected. CS is driven as GPIO, not hardware
NSS, because hardware NSS manages only one slave.

**Wiring to the 326:**

| 326 pin | MCU pin |
|---|---|
| Vin | 3.3 V |
| GND | GND |
| CLK (D0) | PB13 |
| Data (D1) | PB15 |
| CS | PB10 |
| RST | PB11 |
| SA0 (D/C) | PB12 |
| 3Vo | **leave unconnected** |

`3Vo` is an output from the board's own regulator. Connecting it to a rail is the one wiring
mistake here that can damage something.

Check the solder jumpers on the back of the breakout — the 326 supports both SPI and I²C and must
be in SPI mode. Logic is 3.3 V compatible, no level shifting needed.

Don't write the SSD1306 driver; `afiskon/stm32-ssd1306` is MIT-licensed and handles HAL SPI.
Point its config header at the pins above. Redraw only on change, from the main loop, never from
an ISR — a full 1024-byte frame at 10 MHz blocks for about a millisecond, which the step ISR
preempts cleanly at priority 0.

---

## 9. SYS and NVIC

**System Core → SYS → Debug.** Currently **Trace Asynchronous SW**, which reserves PB3 as SWO.
That's a legitimate choice — SWO/ITM tracing is faster than UART printf — but it's the pin
blocking SPI1. Switch to plain **Serial Wire** to free PB3, PB4 and PA15. You already have
LPUART1 for console output, so there's little reason to keep trace.

**NVIC priorities.** Note the vector names: on STM32G4 these interrupts are shared, so neither is
listed under the timer you'd expect.

| Interrupt (as listed in CubeMX) | Preempt priority | Purpose |
|---|---|---|
| TIM1 update interrupt and TIM16 global interrupt | **0** | Step clock — musical timing, must never be delayed |
| TIM1 trigger and commutation interrupts and TIM17 global interrupt | **1** | Gate-off, tolerates microseconds |
| SysTick | 15 (HAL default) | Housekeeping |

If the priority column is greyed out: the peripheral isn't Activated yet, or **System Core → NVIC
→ Priority Group** isn't allocating pre-emption bits. It needs *4 bits for pre-emption priority,
0 bits for subpriority*.

Never call `HAL_Delay()` or anything polling `HAL_GetTick()` from either timer ISR — SysTick is
lower priority and cannot preempt them, so it would deadlock rather than time out.

The vector sharing doesn't affect the firmware: `app.c` dispatches on `htim->Instance` inside
`HAL_TIM_PeriodElapsedCallback()`, so it doesn't care which physical vector delivered the event.

---

## 10. Project Manager, and what's deliberately absent

- Toolchain / IDE: **STM32CubeIDE**
- Code Generator → **Generate peripheral initialization as a pair of .c/.h files per peripheral: ON**
  (seven timers make a single `main.c` unpleasant, and it shrinks the surface a regeneration can clobber)
- Code Generator → **Keep User Code when re-generating: ON**
- Do **not** enable "Set all free pins as analog" — leaves you room to bodge

Not configured, on purpose: no EXTI on the buttons, no DMA, no TIM17 output channel, nothing on
PA5/LD2.

**Disable USART1 if it's still enabled on PA9/PA10.** It doesn't connect to anything on the
Nucleo and consumes two pins that could serve I²C2 later.

**Do not add `HAL_TIM_*_Start()` calls anywhere.** `app_init()` starts all seven timers itself,
in order, with the register tweaks HAL doesn't expose.

### Pins still free

PA5, PA8, PA9, PA10, PA15, PB1, PB4, PB5, PB6, PB7, PB8, PB9, PC8, PC9, PC10, PC11, PC13, PD2.

Useful groupings: **I²C1 on PB6/PB7** is clear. **SPI3** is available on PC10/PC11/PB5 with NSS
on PA15. **SPI1** needs PB3 freed first. For a future daughter-MCU bus, PB10–PB15 was chosen as
one contiguous run — three of those are now the OLED control lines, so a three-CS bus would move
to PB6–PB9 or similar.

---

## 11. Bench verification checklist

Tick these off and update the status banner at the top.

- [ ] Project builds clean. If `_Static_assert` errors, set Dialect to **gnu11**.
- [ ] LPUART1 banner appears over the ST-Link VCP at 115200
- [ ] SYSCLK measures/reports 170 MHz; APB timer clocks 170 MHz
- [ ] Start/Stop produces gates on PC0–PC3
- [ ] Clock out and gate rising edges are **coincident** on a scope — if they're one step period
      apart, the OC-preload disable in `seq_init()` didn't take
- [ ] Step period correct: 8.00 Hz clock out at 120 BPM with 4 steps per beat
- [ ] SOC fires once per N steps, aligned to the first hit of each pattern
- [ ] All five encoders count up clockwise (N is inverted in firmware, not CubeMX)
- [ ] Gate sweep: Shift + Tempo walks 5 %→95 % and hits both stops in one revolution
- [ ] No double-counting on any encoder; raise IC filter to 15 if there is
- [ ] Flash `DBANK` option bit confirmed before first save — see `param_store.c`
- [ ] Save gesture (both buttons, 2 s) reports `PARAM_OK`, survives power cycle
