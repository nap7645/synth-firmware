/**
  ******************************************************************************
  * @file    euclid_config.h
  * @brief   All tunable constants for Euclid 1. Nothing here should require
  *          touching the logic modules -- tune on hardware by editing this file.
  ******************************************************************************
  */
#ifndef EUCLID_CONFIG_H
#define EUCLID_CONFIG_H

/* ---------------------------------------------------------------- timing ---*/

/* TIM1/TIM17 tick rate. PSC = 1699 on a 170 MHz timer clock -> 100 kHz.
   If you change the prescaler in CubeMX, change this to match or every
   tempo and gate calculation will be wrong. */
#define SEQ_TIMER_HZ            100000UL

/* Musical resolution: how many sequencer steps make up one beat (one BPM unit).
   4 = each step is a 16th note, so N=16 is one bar and SOC marks the bar line.

   Do NOT set this to 1 without also raising the prescaler. TIM1 is 16-bit, so
   the longest step period is 65536 ticks = 0.655 s. At 30 BPM one *beat* is 2 s
   (200000 ticks) which overflows; one 16th note is 0.5 s (50000 ticks) which
   fits with room to spare. */
#define SEQ_STEPS_PER_BEAT      4U

/* ------------------------------------------------------------ parameters ---*/

#define SEQ_TEMPO_MIN           30U
#define SEQ_TEMPO_MAX           300U
#define SEQ_TEMPO_DEFAULT       120U

#define SEQ_N_MIN               1U
#define SEQ_N_MAX               64U         /* 1024 in the production version */
#define SEQ_N_DEFAULT           16U

/* Global gate length as a percentage of one step.
   18 PPR encoder x 5 % per detent = 5..95 % in exactly one revolution. */
#define SEQ_GATE_MIN_PCT        5U
#define SEQ_GATE_MAX_PCT        95U
#define SEQ_GATE_STEP_PCT       5U
#define SEQ_GATE_DEFAULT_PCT    50U

#define SEQ_NUM_CHANNELS        3U          /* K1..K3, one bank */

/* Shift + N quantises to this list, one entry per detent. */
#define SEQ_N_QUANT_TABLE       { 1, 2, 4, 8, 16, 32, 64 }
#define SEQ_N_QUANT_COUNT       7U

/* ---------------------------------------------------------------- outputs ---*/

/* Clock output divider, in steps per clock pulse.
   With SEQ_STEPS_PER_BEAT = 4 a step is a 16th note, so:
       1 -> one pulse per step   (16th notes, 8 Hz at 120 BPM)
       4 -> one pulse per beat   (quarter notes, 2 Hz at 120 BPM)  <- BPM clock
       8 -> one pulse per half note
   The divider runs on its own counter, not on the pattern step, so the clock
   stays regular even when N is not a multiple of it. */
#define SEQ_CLOCK_DIV           SEQ_STEPS_PER_BEAT

/* Clock out and SOC share TIM17, so they share one pulse width, and that width
   is the global gate length. If you later want clock out to be a fixed-width
   trigger independent of gate %, set this to a millisecond value and it will be
   used instead -- but note that decoupling clock from SOC needs a second
   one-shot timer (TIM6 is free). */
#define SEQ_TRIGGER_FIXED_MS    0U          /* 0 = follow global gate % */

/* --------------------------------------------------------------- encoders ---*/

#define ENC_COUNTS_PER_DETENT   4           /* x4 quadrature decoding, 18 PPR */

/* Velocity / acceleration. Applied to the TEMPO encoder only.
   30..300 BPM at 1 BPM per detent is 15 full revolutions without this.

   Deliberately NOT applied to gate length: acceleration and riding a knob for
   performance are opposed -- the same wrist movement must give the same result
   every time. Also not applied to N, k or offset, where one detent per value is
   already correct across a 1..64 range. */
#define ENC_ACCEL_FAST_MS       30U         /* detent interval below this -> */
#define ENC_ACCEL_FAST_MULT     10
#define ENC_ACCEL_MED_MS        80U         /* ...and below this -> */
#define ENC_ACCEL_MED_MULT      4

/* ---------------------------------------------------------------- buttons ---*/

#define UI_POLL_INTERVAL_MS     1U
#define UI_DEBOUNCE_MS          5U          /* 1k series to GND, no RC network */
#define UI_SAVE_HOLD_MS         2000U       /* both buttons held -> save */

/* ------------------------------------------------------------- behaviour ---*/

/* When N shrinks, k and offset must be clamped to fit. With this enabled the
   pre-clamp value is remembered, so 16 -> 4 -> 16 restores your original k.
   The remembered value is discarded the moment you turn that encoder, so there
   is never a hidden value that contradicts what you last touched.

   Set to 0 for the blunt behaviour: clamping is destructive and 16 -> 4 -> 16
   leaves k at 4. */
#define EUCLID_RESTORE_K_ON_N_GROW  1

/* Boot stopped, with the last saved pattern recalled. */
#define SEQ_BOOT_RUNNING        0

/* ---------------------------------------------------------------- display ---*/

/* Set to 1 once an SSD1306 driver is in the tree. At 0 the display module
   compiles to no-ops and needs no external dependencies. */
#define EUCLID_DISPLAY          0

/* Floor on redraw rate. 20 ms caps the display at 50 Hz, which is well above
   the step rate at any tempo and keeps SPI off the bus the rest of the time. */
#define DISPLAY_MIN_INTERVAL_MS 20

/* ------------------------------------------------------------------ debug ---*/

/* Set to 1 to get printf over the ST-Link VCP. The Nucleo BSP owns LPUART1 on
   PA2/PA3 as hcom_uart[COM1]; 115200 8N1. */
#define EUCLID_DEBUG_UART       1

#endif /* EUCLID_CONFIG_H */
