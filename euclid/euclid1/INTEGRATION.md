# Euclid 1 — build, flash, bring-up

Integration is **done**. The app modules are in `Euclid1-V1/Core/Src` and `Core/Inc` alongside
the CubeMX-generated files, and `main.c` is wired up. This document is what to do next.

## Project layout

```
euclid1/
├── EUCLID1_CUBEMX_CONFIG.md      peripheral config reference
├── INTEGRATION.md                this file
└── Euclid1-V1/                   the CubeIDE project
    ├── Euclid1-V1.ioc
    ├── Core/Src/  main.c stm32g4xx_it.c stm32g4xx_hal_msp.c syscalls.c ...
    │               app.c display.c encoder.c euclid_gen.c param_store.c
    │               seq_engine.c ui.c
    └── Core/Inc/  main.h ...
                    app.h display.h encoder.h euclid_config.h euclid_gen.h
                    param_store.h seq_engine.h ui.h
```

CubeIDE indexes `Core/Src` and `Core/Inc` automatically. If it doesn't see the new files,
select the project and press **F5**.

## What was wired into main.c

Three edits, all inside `USER CODE` blocks so regenerating from the `.ioc` won't destroy them:

```c
/* USER CODE BEGIN Includes */
#include "app.h"
/* USER CODE END Includes */
```

```c
  /* USER CODE BEGIN WHILE */
  app_init();

  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
    app_tick();
  }
  /* USER CODE END 3 */
```

**`app_init()` is in USER CODE BEGIN WHILE, not USER CODE 2, and that placement matters.**
`BSP_COM_Init()` sits between the two, and it is what configures LPUART1. Calling `app_init()`
from USER CODE 2 would print the startup banner into an unconfigured UART and you'd see nothing.

## Debug console

The Nucleo BSP owns LPUART1 on PA2/PA3 as `hcom_uart[COM1]` — there is no `MX_LPUART1_Init()`
and no `hlpuart1` handle. `app.c` borrows the BSP's handle for `__io_putchar()`.

To enable: set `EUCLID_DEBUG_UART` to 1 in `euclid_config.h`, then open the ST-Link virtual COM
port at 115200:

```bash
screen /dev/tty.usbmodem* 115200
```

`setvbuf(stdout, NULL, _IONBF, 0)` runs before the first `printf` — without it, short output sits
in the stdio buffer and never appears, which looks exactly like code that didn't run.

Turn it back off before scoping any timing. The blocking `HAL_UART_Transmit` is slow enough to
distort measurements.

## Flashing

**First flash, module out of the rack.** The Nucleo takes power from the Eurorack rails through
the morpho headers and the ST-Link supplies power over USB — two sources on one rail, resolved by
a jumper whose position should be established deliberately, not discovered.

1. Remove the module from the rack. No Eurorack power.
2. USB to the ST-Link port.
3. **Run → Debug As → STM32 C/C++ Application.** Accept the ST-Link firmware update if offered.
4. It halts at the top of `main()`. **F8** to resume.
5. Disconnect USB, then install and power from the rack.

Once you've confirmed the jumper arrangement, flashing in-rack is far more convenient.

Use **Run As** rather than Debug As for flash-and-go once it works.

If the debugger can't connect after a bad flash: debug configuration → Debugger tab → Reset
behaviour → **Connect under reset**. That halts the core before your code runs. You have almost
certainly not bricked anything.

## Bring-up order

Each step isolates one failure mode. Don't skip ahead.

1. **Build clean.** If `_Static_assert` errors, Project → Properties → C/C++ Build → Settings →
   MCU GCC Compiler → General → Language standard → **gnu11**.
2. **Banner appears** over the VCP. That one line confirms the clock configured, `app_init()` was
   reached, and flash recall ran.
3. **Press Start/Stop.** Defaults are 120 BPM, N=16, k = 4/5/7, gate 50 %. Expect four evenly
   spaced gates per 16 steps on PC0, SUM busiest on PC3, SOC once per cycle on PC4.
4. **Scope a gate against clock out.** Rising edges must be **coincident**. One step period of
   offset means the OC-preload disable in `seq_init()` didn't take.
5. **Step rate.** 120 BPM at 4 steps/beat = 8.00 Hz on clock out, 125 ms per step. A 4× error
   means `SEQ_STEPS_PER_BEAT` isn't what you expected; a smooth percentage error means
   `SEQ_TIMER_HZ` disagrees with the actual prescaler.
6. **Gate sweep.** Hold Shift, turn Tempo. One full revolution should walk 5 %→95 % and hit both
   stops. More or less than a revolution means the encoder isn't 18 PPR and `SEQ_GATE_STEP_PCT`
   needs adjusting.
7. **Encoder directions.** All five should increase clockwise. N is already inverted in
   `ui_init()`; if any *other* one reads backwards, flip its `invert` argument there.
8. **Double-counting.** If any encoder skips or doubles, raise `IC1Filter`/`IC2Filter` from 8 to
   15 in CubeMX.
9. **Save last** — read the flash warning below first.

## Before your first save: verify the flash page

`param_store.c` erases **bank 2, page 127, at 0x0807F800**, which assumes option bit `DBANK = 1`
(dual bank, 2 KB pages). That's the factory default for G474RE but is **not verified on your
part**.

Check in STM32CubeProgrammer → Option Bytes → User Configuration → **DBANK**.

- `DBANK = 1` → constants are correct, no change.
- `DBANK = 0` → single bank, 4 KB pages. Change to `FLASH_BANK_1`, page 127, `0x0807F000`.

Getting it wrong won't brick the part — it erases a page of program flash you aren't using — but
saves will silently fail. `param_store_save()` reads back and verifies, so
`ui_last_save_result()` will tell you.

Save gesture: hold **Shift first**, then Start/Stop, for 2 seconds. It stops the sequencer before
erasing, because a page erase stalls the bus for tens of milliseconds and would be audible.

## Host-side test of the pattern generator

`euclid_gen.c` has no hardware dependency, so it tests on your laptop:

```bash
cd Euclid1-V1/Core/Src
cc -std=c99 -I../Inc -DEUCLID_GEN_TEST euclid_gen.c -o /tmp/egt && /tmp/egt
```

Asserts, for every n in 1..64 and every k in 0..n, that the hit count equals k, the downbeat is
present whenever k > 0, and rotation is cyclic with period n. Then prints known patterns to
eyeball. Re-run any time you touch that file.

## Display

`display.c` is a **stub**. `EUCLID_DISPLAY` is 0, so it compiles to no-ops and needs no
dependencies. To bring it up: add an SSD1306 driver, point it at SPI2 and PB10/PB11/PB12, set
`EUCLID_DISPLAY` to 1, fill in the `oled_*` porting functions and then `draw_overview()`.

Redraw only on change, from the main loop, never from an ISR.
