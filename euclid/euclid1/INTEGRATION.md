# Dropping these files into the CubeIDE project

## 1. Copy

```bash
cd ~/Desktop
cp "Euclid Firmware Files/euclid1_src/Core/Inc/"*.h  synth-firmware/euclid/euclid1/Core/Inc/
cp "Euclid Firmware Files/euclid1_src/Core/Src/"*.c  synth-firmware/euclid/euclid1/Core/Src/
cp "Euclid Firmware Files/EUCLID1_CONTEXT.md" \
   "Euclid Firmware Files/EUCLID1_CUBEMX_CONFIG.md" \
   synth-firmware/docs/
```

CubeIDE indexes `Core/Src` and `Core/Inc` automatically — no build-config changes needed.
Right-click the project → Refresh (F5) if it doesn't notice.

## 2. Two edits to `main.c`

Both inside `USER CODE` blocks, so regenerating from the `.ioc` won't touch them.

```c
/* USER CODE BEGIN Includes */
#include "app.h"
/* USER CODE END Includes */
```

```c
  /* USER CODE BEGIN 2 */
  app_init();
  /* USER CODE END 2 */

  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
    app_tick();
  }
  /* USER CODE END 3 */
```

That is the entire integration. Everything else lives in the app modules.

## 3. Do NOT let CubeMX start the timers

`app_init()` starts TIM1, TIM17 and all five encoder timers itself, in the right order and with
the register tweaks that HAL doesn't expose. Don't add `HAL_TIM_*_Start()` calls of your own.

## 4. Bring-up order

Work through this in sequence — each step isolates one failure mode.

1. **Build clean.** If `_Static_assert` errors, your project is on C99: set
   Project → Properties → C/C++ Build → Settings → MCU GCC Compiler → Dialect to **gnu11**.
2. **Confirm it runs.** Set `EUCLID_DEBUG_UART 1` in `euclid_config.h`, open the ST-Link VCP at
   115200, and check you get the banner. Turn it back off before measuring timing — the blocking
   UART transmit in `__io_putchar` is slow enough to matter.
3. **Press Start/Stop.** With defaults (120 BPM, N=16, k = 4/5/7, gate 50 %) you should see
   K1 on PC0 firing four evenly spaced gates per 16 steps, SUM on PC3 busiest, SOC on PC4 once
   per 16 steps.
4. **Scope one gate against clock out.** Rising edges must be coincident. If clock out leads or
   lags by exactly one step period, the OC-preload disable in `seq_init()` didn't take.
5. **Check the step period.** At 120 BPM with 4 steps per beat, one step is 125 ms and clock out
   should read 8.00 Hz. If it's 4x off, `SEQ_STEPS_PER_BEAT` disagrees with what you expected.
   If it's off by a smooth percentage, `SEQ_TIMER_HZ` doesn't match your actual prescaler.
6. **Sweep gate length.** Hold Shift, turn Tempo. One full revolution should walk 5 % to 95 %
   and hit both end stops. If it takes more or less than a revolution, your encoder isn't 18 PPR
   and `SEQ_GATE_STEP_PCT` needs adjusting.
7. **Check every encoder direction.** All five should increase clockwise. The N encoder is
   already inverted in `ui_init()`; if any *other* one reads backwards, flip its `invert`
   argument there rather than rewiring.
8. **Save last.** See the warning below before the first save.

## 5. Before your first save — verify the flash page

`param_store.c` erases **bank 2, page 127, at 0x0807F800**, which assumes the option bit
`DBANK = 1` (dual bank, 2 KB pages). That is the factory default for G474RE but it is not
verified on your part.

Check in STM32CubeProgrammer → Option Bytes → User Configuration → **DBANK**.

- `DBANK = 1` → the constants in `param_store.c` are correct, no change needed.
- `DBANK = 0` → single bank, 4 KB pages. Change to `FLASH_BANK_1`, page `127`,
  address `0x0807F000`.

Getting this wrong won't brick the part — it will erase a page of program flash you're not using
at 41 KB of code — but it will make saves silently fail. `param_store_save()` reads back and
verifies, so `ui_last_save_result()` will tell you.

## 6. Host-side test of the pattern generator

The generator has no hardware dependency, so you can test it without the board:

```bash
cd synth-firmware/euclid/euclid1/Core/Src
cc -std=c99 -I../Inc -DEUCLID_GEN_TEST euclid_gen.c -o /tmp/egt && /tmp/egt
```

It asserts, for every n in 1..64 and every k in 0..n, that the hit count equals k, that the
downbeat is always present when k > 0, and that rotation is cyclic with period n. Then it prints
some known patterns to eyeball. Worth re-running any time you touch that file.
