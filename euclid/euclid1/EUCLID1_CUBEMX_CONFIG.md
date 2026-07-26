# Euclid 1 — STM32CubeMX Configuration Checklist

Board: **NUCLEO-G474RE**. Start a new project from the *Board Selector* (not MCU selector) so
the ST-Link, LD2 and B1 defaults come in correctly. When asked "Initialize all peripherals with
their default Mode?" answer **No** — we want a clean sheet.

Project name: `euclid1`, location `~/Desktop/synth-firmware/euclid/`, toolchain **STM32CubeIDE**.

---

## 1. Clock configuration (RCC)

Matches the existing firmware exactly — 170 MHz.

| Setting | Value |
|---|---|
| Oscillator | HSI (16 MHz), no HSE |
| PLL source | HSI |
| PLLM | /4 |
| PLLN | 85 |
| PLLP / PLLQ / PLLR | /2 |
| SYSCLK source | PLLCLK |
| AHB prescaler | /1 |
| APB1 / APB2 prescaler | /1 |
| Voltage scaling | Range 1 **Boost** |
| Flash latency | 4 WS |

Result: SYSCLK 170 MHz, **timer clock 170 MHz on both APBs**.
With PSC = 1699 that gives a 100 kHz timer tick (170e6 / 1700), same as all existing code.

---

## 2. TIM1 — master step clock + 4 gate outputs

- Clock Source: **Internal Clock**
- Channel1: **PWM Generation CH1** -> PC0 (K1)
- Channel2: **PWM Generation CH2** -> PC1 (K2)
- Channel3: **PWM Generation CH3** -> PC2 (K3)
- Channel4: **PWM Generation CH4** -> PC3 (SUM)

Parameter Settings:

| Field | Value |
|---|---|
| Prescaler | 1699 |
| Counter Mode | Up |
| Counter Period (ARR) | 11539 (placeholder; set at runtime from tempo) |
| auto-reload preload | **Enable** |
| Trigger Event Selection | Update Event |

NVIC: enable **TIM1 update interrupt**, preemption priority **0**.

> In code, set `TIM1->CR1 |= TIM_CR1_URS` before starting — this stops a software-generated
> update event from spuriously firing the step ISR (your DesignExpoDemo already does this).

Verify pin AF: PC0-PC3 must show `TIM1_CH1..CH4` (AF2). If CubeMX offers PA8-PA11 instead,
click the pin and pick the PCx alternative from the dropdown.

---

## 3. TIM2 / TIM3 / TIM4 / TIM5 / TIM8 — five quadrature encoders

For **each** timer: Combined Channels -> **Encoder Mode**.

| Timer | CH1 pin | CH2 pin | AF | Encoder |
|---|---|---|---|---|
| TIM2 | PA0 | PA1 | AF1 | Tempo |
| TIM3 | PA6 | **PA4** | AF2 | N — note A leg is on CH2, direction inverted |
| TIM4 | PA11 | PA12 | AF10 | K1 |
| TIM5 | PB2 | PC12 | PB2=AF2, PC12=AF1 | K2 |
| TIM8 | PC6 | PC7 | AF4 | K3 |

Identical Parameter Settings for all five:

| Field | Value |
|---|---|
| Prescaler | 0 |
| Counter Period (ARR) | **65535** |
| Encoder Mode | **Encoder Mode TI1 and TI2** (x4) |
| IC1/IC2 Polarity | Rising Edge |
| IC1/IC2 Prescaler | Division 1 |
| **IC1/IC2 Filter** | **8** |

Two deliberate departures from your earlier encoder code:

1. **ARR 65535, not 71.** Your gate-timer sketch used ARR=71 so one revolution mapped
   absolutely onto the full range (18 PPR x4 = 72 counts). That's elegant for a dedicated knob
   but breaks here because Tempo and gate length share one encoder. We read *deltas* instead
   and let the counter free-run, so it must not wrap early.
2. **Input filter 8.** Mechanical encoders bounce; the hardware filter kills the miscounts that
   otherwise show up as jitter. Costs nothing. Raise to 15 if you still see double-counts.

No NVIC interrupts needed — these are polled.

---

## 4. TIM17 — one-shot pulse terminator

> **AMENDED after writing the firmware.** TIM17 no longer needs a PWM channel, and **PA7 is now
> a plain GPIO output**, not `TIM17_CH1`. Reason below. If you already configured PA7 as
> `TIM17_CH1`, change it.

- Clock Source: **Internal Clock**
- **No channels configured** — base timer only

| Field | Value |
|---|---|
| Prescaler | 1699 |
| Counter Period (ARR) | 11539 (placeholder; set per step at runtime) |
| One Pulse Mode | **Enable** (set in code too, so it's fine if the checkbox is absent) |
| auto-reload preload | **Disable** — ARR must take effect immediately |

NVIC: enable **TIM17 global interrupt**, preemption priority **1**.

**Why this changed.** PA7's only free timer channel is `TIM17_CH1` (TIM3 is taken by the N
encoder), and TIM17 has no slave-mode controller, so it can't be hardware-synced to TIM1. That
left a choice between fighting one-pulse PWM polarity semantics for one output while SOC on PC4
had to be software-driven anyway, or driving both the same way. Driving both as GPIO is simpler,
makes clock out and SOC bit-for-bit consistent with each other, and removes the pin from the AF
constraint entirely. TIM17 now exists only to fire an interrupt that drops both pins at the end
of the gate.

---

## 5. GPIO

| Pin | Mode | Pull | Extra | Function |
|---|---|---|---|---|
| PC4 | GPIO_Output | No pull | Push-pull, Low speed, initial **LOW** | SOC out |
| **PA7** | **GPIO_Output** | No pull | Push-pull, **High** speed, initial **LOW** | Clock out (amended) |
| PB0 | GPIO_Input | **Pull-up** | — | Start/Stop (active low) |
| PC5 | GPIO_Input | **Pull-up** | — | Shift (active low) |

Both buttons are wired through 1k to GND with no RC network, so internal pull-ups are required
and debounce is in software.

**Polled, not EXTI** — deliberate. The 2-second both-buttons-held save gesture needs continuous
state sampling anyway, so edge interrupts would just add a second code path to keep in sync.

Leave PA5 (LD2) unconfigured; the save indicator moved to the Euclid 3 display.

---

## 6. USART2 — debug console (recommended)

- Mode: **Asynchronous**, 115200 8N1, no flow control
- Pins: PA2 (TX), PA3 (RX) — these route to the ST-Link virtual COM port

Not required for function, but during bring-up being able to `printf` the pattern words, step
index and computed ARR will save you hours versus inferring state from six LEDs. Nothing else
in the design uses PA2/PA3.

---

## 7. NVIC priority summary

| Interrupt | Preempt priority | Why |
|---|---|---|
| TIM1 update | 0 | Step clock — must never be delayed, this is musical timing |
| TIM17 | 1 | Gate-off, tolerates microseconds of latency |
| SysTick | 15 (HAL default) | Housekeeping only |

Never call `HAL_Delay()` or anything that waits on `HAL_GetTick()` from the TIM1 or TIM17 ISR —
SysTick is lower priority and will never preempt them, so it would deadlock.

---

## 8. Project Manager

- Toolchain / IDE: **STM32CubeIDE**
- Code Generator -> **Generate peripheral initialization as a pair of .c/.h files per peripheral: ON**

With eight timers configured, a single `main.c` gets unwieldy. Turning this on keeps CubeMX's
generated code in `tim.c`, `gpio.c`, `usart.c` and leaves `main.c` short. Cosmetic, but it also
shrinks the surface area where a regeneration can clobber hand-written code.

- Code Generator -> **Keep User Code when re-generating: ON**
- Do **not** enable "Set all free pins as analog"

---

## 9. After generating

Confirm it builds clean, then flash and check with a scope or LEDs that PC0-PC3 produce PWM at
all. Once that's confirmed, the five app modules go in `Core/Src` and `Core/Inc`:

`euclid_gen` · `encoder` · `seq_engine` · `ui` · `param_store`

## Open item

`param_store` will use the last flash page. STM32G474RE is 512 KB; page size is 2 KB in
dual-bank mode. Exact page index and base address to be confirmed against RM0440 before
writing the flash driver — do not trust this figure yet.
