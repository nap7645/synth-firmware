/**
  ******************************************************************************
  * @file    seq_engine.h
  * @brief   Sequencer state and step scheduling for one bank.
  *
  * One bank = three Euclidean patterns (K1..K3) sharing a single length N,
  * each with its own hit count k and rotation offset, plus a SUM output that is
  * the logical OR of the three, and an SOC output that fires on step 0.
  *
  * Euclid 3 becomes an array of these.
  ******************************************************************************
  */
#ifndef SEQ_ENGINE_H
#define SEQ_ENGINE_H

#include "main.h"
#include "euclid_config.h"
#include <stdint.h>

typedef struct
{
    /* ---- user parameters ------------------------------------------------- */
    uint16_t tempo_bpm;                     /* SEQ_TEMPO_MIN..SEQ_TEMPO_MAX */
    uint8_t  n;                             /* SEQ_N_MIN..SEQ_N_MAX */
    uint8_t  gate_pct;                      /* SEQ_GATE_MIN_PCT..MAX_PCT */
    uint8_t  k[SEQ_NUM_CHANNELS];           /* effective, always <= n-1 */
    uint8_t  offset[SEQ_NUM_CHANNELS];      /* effective, always <  n */

    /* Pre-clamp values the user actually dialled in, restored if N grows back.
       See EUCLID_RESTORE_K_ON_N_GROW. */
    uint8_t  k_intent[SEQ_NUM_CHANNELS];
    uint8_t  offset_intent[SEQ_NUM_CHANNELS];

    /* ---- generated patterns, read by the step ISR ------------------------ */
    uint64_t pat[SEQ_NUM_CHANNELS];
    uint64_t pat_sum;

    /* ---- transport ------------------------------------------------------- */
    volatile uint8_t  running;
    volatile uint8_t  step;                 /* 0..n-1, next step to play */
    volatile uint8_t  dirty;                /* patterns need regenerating */

    /* ---- derived, recomputed when tempo or gate changes ------------------ */
    volatile uint16_t arr;                  /* TIM1 period - 1 */
    volatile uint16_t gate_ticks;           /* CCR value for an active gate */
} seq_state_t;

extern seq_state_t seq;

/**
  * @brief  Load defaults, recall saved parameters, generate patterns and start
  *         the timers. Call once from app_init().
  */
void seq_init(TIM_HandleTypeDef *htim_step, TIM_HandleTypeDef *htim_trig);

/**
  * @brief  Regenerate patterns if a parameter changed. Call from the main loop,
  *         never from an ISR -- it does the 64-bit work with interrupts briefly
  *         masked so the ISR never sees a half-written pattern.
  */
void seq_service(void);

/* -------------------------------------------------------------- transport ---*/

void seq_start(void);          /* run from step 0 */
void seq_stop(void);           /* stop and reset to step 0 */
void seq_pause_toggle(void);   /* stop or resume in place, preserving step */
void seq_toggle(void);         /* Start/Stop button: stop-and-reset semantics */

/* ------------------------------------------------------------- parameters ---*/
/* Each takes a signed detent delta and clamps internally. */

void seq_adjust_tempo(int32_t d);
void seq_adjust_gate(int32_t d);
void seq_adjust_n(int32_t d);
void seq_adjust_n_quantised(int32_t d);
void seq_adjust_k(uint8_t ch, int32_t d);
void seq_adjust_offset(uint8_t ch, int32_t d);

/* ---------------------------------------------------------------- ISR body ---*/

void seq_on_step(void);         /* from TIM1 update */
void seq_on_trigger_end(void);  /* from TIM17 update */

#endif /* SEQ_ENGINE_H */
