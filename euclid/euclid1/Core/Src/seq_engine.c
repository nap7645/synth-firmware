/**
  ******************************************************************************
  * @file    seq_engine.c
  * @brief   Sequencer state and step scheduling for one bank.
  ******************************************************************************
  */

#include "seq_engine.h"
#include "euclid_gen.h"
#include "param_store.h"

/* Output pins. Deliberately raw rather than CubeMX labels so this module does
   not care what you named them in the .ioc. */
#define SOC_PORT        GPIOC
#define SOC_PIN         GPIO_PIN_4
#define CLK_PORT        GPIOA
#define CLK_PIN         GPIO_PIN_7

seq_state_t seq;

static TIM_HandleTypeDef *h_step;       /* TIM1  -- master step clock + gates */
static TIM_HandleTypeDef *h_trig;       /* TIM17 -- one-shot, ends SOC + clock */

static const uint8_t n_quant[SEQ_N_QUANT_COUNT] = SEQ_N_QUANT_TABLE;

/* -------------------------------------------------------------------------- */
/* helpers                                                                    */
/* -------------------------------------------------------------------------- */

static int32_t clampi(int32_t v, int32_t lo, int32_t hi)
{
    return (v < lo) ? lo : ((v > hi) ? hi : v);
}

/**
  * @brief  Step period in timer ticks for a given tempo.
  *
  *   ticks = SEQ_TIMER_HZ * 60 / (bpm * steps_per_beat)
  *
  * At 100 kHz and 4 steps per beat this spans 50000 ticks (30 BPM) down to
  * 5000 ticks (300 BPM), comfortably inside TIM1's 16-bit range.
  */
static uint16_t arr_from_tempo(uint16_t bpm)
{
    if (bpm < SEQ_TEMPO_MIN) bpm = SEQ_TEMPO_MIN;
    if (bpm > SEQ_TEMPO_MAX) bpm = SEQ_TEMPO_MAX;

    uint32_t ticks = (SEQ_TIMER_HZ * 60UL) / ((uint32_t)bpm * SEQ_STEPS_PER_BEAT);

    if (ticks < 2UL)     ticks = 2UL;
    if (ticks > 65536UL) ticks = 65536UL;   /* would need a bigger prescaler */

    return (uint16_t)(ticks - 1UL);
}

static void recalc_timing(void)
{
    uint16_t arr    = arr_from_tempo(seq.tempo_bpm);
    uint32_t period = (uint32_t)arr + 1UL;

    uint32_t gate;
#if (SEQ_TRIGGER_FIXED_MS > 0U)
    (void)period;
    gate = (SEQ_TIMER_HZ / 1000UL) * SEQ_TRIGGER_FIXED_MS;
#else
    gate = (period * seq.gate_pct) / 100UL;
#endif

    if (gate < 1UL)          gate = 1UL;
    if (gate > period - 1UL) gate = period - 1UL;

    seq.arr        = arr;
    seq.gate_ticks = (uint16_t)gate;

    /* ARR preload is enabled on TIM1, so this lands at the next update event
       and the step currently in flight is never truncated. */
    __HAL_TIM_SET_AUTORELOAD(h_step, arr);
}

/**
  * @brief  Re-clamp k and offset after N changes, honouring the intent shadow.
  */
static void reclamp_channels(void)
{
    uint8_t kmax = (seq.n > 1U) ? (uint8_t)(seq.n - 1U) : 0U;

    for (uint8_t c = 0; c < SEQ_NUM_CHANNELS; c++)
    {
#if EUCLID_RESTORE_K_ON_N_GROW
        seq.k[c]      = (seq.k_intent[c]      > kmax)          ? kmax
                                                              : seq.k_intent[c];
        seq.offset[c] = (seq.offset_intent[c] >= seq.n)
                            ? (uint8_t)(seq.offset_intent[c] % seq.n)
                            : seq.offset_intent[c];
#else
        if (seq.k[c] > kmax)        seq.k[c]      = kmax;
        if (seq.offset[c] >= seq.n) seq.offset[c] = (uint8_t)(seq.offset[c] % seq.n);
        seq.k_intent[c]      = seq.k[c];
        seq.offset_intent[c] = seq.offset[c];
#endif
    }
}

static void zero_all_gates(void)
{
    __HAL_TIM_SET_COMPARE(h_step, TIM_CHANNEL_1, 0);
    __HAL_TIM_SET_COMPARE(h_step, TIM_CHANNEL_2, 0);
    __HAL_TIM_SET_COMPARE(h_step, TIM_CHANNEL_3, 0);
    __HAL_TIM_SET_COMPARE(h_step, TIM_CHANNEL_4, 0);
    HAL_GPIO_WritePin(SOC_PORT, SOC_PIN, GPIO_PIN_RESET);
    HAL_GPIO_WritePin(CLK_PORT, CLK_PIN, GPIO_PIN_RESET);
}

/* -------------------------------------------------------------------------- */
/* init                                                                       */
/* -------------------------------------------------------------------------- */

static void load_defaults(void)
{
    seq.tempo_bpm = SEQ_TEMPO_DEFAULT;
    seq.n         = SEQ_N_DEFAULT;
    seq.gate_pct  = SEQ_GATE_DEFAULT_PCT;

    /* Something audible and recognisably Euclidean on a virgin board. */
    static const uint8_t k_def[SEQ_NUM_CHANNELS] = { 4, 5, 7 };

    for (uint8_t c = 0; c < SEQ_NUM_CHANNELS; c++)
    {
        seq.k[c] = seq.k_intent[c] = k_def[c];
        seq.offset[c] = seq.offset_intent[c] = 0U;
    }
}

void seq_init(TIM_HandleTypeDef *htim_step, TIM_HandleTypeDef *htim_trig)
{
    h_step = htim_step;
    h_trig = htim_trig;

    load_defaults();
    param_store_load(&seq);             /* silently keeps defaults if no valid save */
    reclamp_channels();

    seq.step    = 0U;
    seq.running = SEQ_BOOT_RUNNING;
    seq.dirty   = 1U;

    /* --- TIM1: master step clock -------------------------------------------
       URS = 1 so a software-generated update event cannot fire the step ISR. */
    h_step->Instance->CR1 |= TIM_CR1_URS;

    /* Disable output-compare preload on all four gate channels.

       HAL_TIM_PWM_ConfigChannel() enables OCxPE, which would make every CCR
       write take effect one period late. That is fine when you only ever write
       gates, but SOC and clock out are GPIO writes that take effect
       immediately, so the two would drift a full step apart. With preload off,
       everything the step ISR touches applies to the period that is starting
       right now.

       Safe because the ISR runs with CNT close to 0, thousands of ticks before
       the smallest gate compare value (5 % of 5000 = 250 ticks = 2.5 ms). */
    h_step->Instance->CCMR1 &= ~(TIM_CCMR1_OC1PE | TIM_CCMR1_OC2PE);
    h_step->Instance->CCMR2 &= ~(TIM_CCMR2_OC3PE | TIM_CCMR2_OC4PE);

    /* --- TIM17: one-shot that ends the SOC and clock pulses ---------------- */
    h_trig->Instance->CR1 |=  TIM_CR1_OPM;      /* stop counting after update */
    h_trig->Instance->CR1 &= ~TIM_CR1_ARPE;     /* ARR writes take effect now */
    __HAL_TIM_CLEAR_IT(h_trig, TIM_IT_UPDATE);
    __HAL_TIM_ENABLE_IT(h_trig, TIM_IT_UPDATE);

    seq_service();                      /* generate patterns before first tick */
    recalc_timing();

    zero_all_gates();

    HAL_TIM_PWM_Start(h_step, TIM_CHANNEL_1);
    HAL_TIM_PWM_Start(h_step, TIM_CHANNEL_2);
    HAL_TIM_PWM_Start(h_step, TIM_CHANNEL_3);
    HAL_TIM_PWM_Start(h_step, TIM_CHANNEL_4);
    HAL_TIM_Base_Start_IT(h_step);
}

/* -------------------------------------------------------------------------- */
/* pattern generation                                                         */
/* -------------------------------------------------------------------------- */

void seq_service(void)
{
    if (!seq.dirty) return;
    seq.dirty = 0U;

    uint64_t p[SEQ_NUM_CHANNELS];
    uint64_t sum = 0ULL;

    for (uint8_t c = 0; c < SEQ_NUM_CHANNELS; c++)
    {
        p[c] = euclid_rotate(euclid_generate(seq.n, seq.k[c]), seq.n, seq.offset[c]);
        sum |= p[c];
    }

    /* 64-bit stores are not atomic on Cortex-M4, and the step ISR reads these.
       Mask interrupts for the handful of cycles it takes to publish them. */
    uint32_t primask = __get_PRIMASK();
    __disable_irq();
    for (uint8_t c = 0; c < SEQ_NUM_CHANNELS; c++) seq.pat[c] = p[c];
    seq.pat_sum = sum;
    if (seq.step >= seq.n) seq.step = 0U;    /* N may have shrunk under us */
    __set_PRIMASK(primask);
}

/* -------------------------------------------------------------------------- */
/* transport                                                                  */
/* -------------------------------------------------------------------------- */

void seq_start(void)
{
    seq.step    = 0U;
    seq.running = 1U;
}

void seq_stop(void)
{
    seq.running = 0U;
    seq.step    = 0U;
    zero_all_gates();
}

void seq_toggle(void)
{
    /* Stop resets to step 0, so downstream gear re-syncs to SOC on every
       restart. Pause-in-place lives on Shift + Start/Stop. */
    if (seq.running) seq_stop();
    else             seq_start();
}

void seq_pause_toggle(void)
{
    if (seq.running)
    {
        seq.running = 0U;               /* step deliberately preserved */
        zero_all_gates();
    }
    else
    {
        seq.running = 1U;
    }
}

/* -------------------------------------------------------------------------- */
/* parameter adjustment                                                       */
/* -------------------------------------------------------------------------- */

void seq_adjust_tempo(int32_t d)
{
    if (d == 0) return;
    seq.tempo_bpm = (uint16_t)clampi((int32_t)seq.tempo_bpm + d,
                                     SEQ_TEMPO_MIN, SEQ_TEMPO_MAX);
    recalc_timing();
}

void seq_adjust_gate(int32_t d)
{
    if (d == 0) return;
    int32_t pct = (int32_t)seq.gate_pct + d * (int32_t)SEQ_GATE_STEP_PCT;
    seq.gate_pct = (uint8_t)clampi(pct, SEQ_GATE_MIN_PCT, SEQ_GATE_MAX_PCT);
    recalc_timing();
}

void seq_adjust_n(int32_t d)
{
    if (d == 0) return;
    uint8_t old = seq.n;
    seq.n = (uint8_t)clampi((int32_t)seq.n + d, SEQ_N_MIN, SEQ_N_MAX);
    if (seq.n == old) return;

    reclamp_channels();
    seq.dirty = 1U;
}

void seq_adjust_n_quantised(int32_t d)
{
    if (d == 0) return;

    /* Find where we sit in the table, rounding down, then walk from there. */
    int32_t idx = 0;
    for (int32_t i = 0; i < (int32_t)SEQ_N_QUANT_COUNT; i++)
        if (n_quant[i] <= seq.n) idx = i;

    idx = clampi(idx + d, 0, (int32_t)SEQ_N_QUANT_COUNT - 1);

    uint8_t old = seq.n;
    seq.n = n_quant[idx];
    if (seq.n == old) return;

    reclamp_channels();
    seq.dirty = 1U;
}

void seq_adjust_k(uint8_t ch, int32_t d)
{
    if (d == 0 || ch >= SEQ_NUM_CHANNELS) return;

    uint8_t kmax = (seq.n > 1U) ? (uint8_t)(seq.n - 1U) : 0U;
    uint8_t nk   = (uint8_t)clampi((int32_t)seq.k[ch] + d, 0, (int32_t)kmax);
    if (nk == seq.k[ch]) return;

    seq.k[ch] = nk;
    /* Touching the encoder discards the shadow: what you see is what you get. */
    seq.k_intent[ch] = nk;
    seq.dirty = 1U;
}

void seq_adjust_offset(uint8_t ch, int32_t d)
{
    if (d == 0 || ch >= SEQ_NUM_CHANNELS) return;

    /* Offset wraps rather than clamping -- it is a rotation, so the ends meet. */
    int32_t o = ((int32_t)seq.offset[ch] + d) % (int32_t)seq.n;
    if (o < 0) o += (int32_t)seq.n;

    if ((uint8_t)o == seq.offset[ch]) return;

    seq.offset[ch]        = (uint8_t)o;
    seq.offset_intent[ch] = (uint8_t)o;
    seq.dirty = 1U;
}

/* -------------------------------------------------------------------------- */
/* interrupt bodies                                                           */
/* -------------------------------------------------------------------------- */

/**
  * @brief  TIM1 update: one sequencer step. Priority 0, keep it short.
  */
void seq_on_step(void)
{
    if (!seq.running)
    {
        zero_all_gates();
        return;
    }

    uint8_t  s = seq.step;
    uint16_t g = seq.gate_ticks;

    /* Gates for the period starting now (OC preload is off, see seq_init). */
    __HAL_TIM_SET_COMPARE(h_step, TIM_CHANNEL_1, ((seq.pat[0] >> s) & 1ULL) ? g : 0U);
    __HAL_TIM_SET_COMPARE(h_step, TIM_CHANNEL_2, ((seq.pat[1] >> s) & 1ULL) ? g : 0U);
    __HAL_TIM_SET_COMPARE(h_step, TIM_CHANNEL_3, ((seq.pat[2] >> s) & 1ULL) ? g : 0U);
    __HAL_TIM_SET_COMPARE(h_step, TIM_CHANNEL_4, ((seq.pat_sum >> s) & 1ULL) ? g : 0U);

    /* SOC marks the downbeat; clock out fires every step. Both are software
       driven -- PC4 has no compare channel at all (TIM1_ETR only) and PA7's
       remaining option collided with the N encoder's timer. */
    if (s == 0U) SOC_PORT->BSRR = SOC_PIN;
    CLK_PORT->BSRR = CLK_PIN;

    /* Arm the one-shot that will drop both of them again. */
    h_trig->Instance->ARR = (g > 0U) ? (uint32_t)(g - 1U) : 0U;
    h_trig->Instance->CNT = 0U;
    h_trig->Instance->SR  = 0U;
    h_trig->Instance->CR1 |= TIM_CR1_CEN;

    if (++seq.step >= seq.n) seq.step = 0U;
}

/**
  * @brief  TIM17 update: end of the SOC / clock pulse. OPM has already stopped
  *         the counter by the time we get here.
  */
void seq_on_trigger_end(void)
{
    SOC_PORT->BSRR = (uint32_t)SOC_PIN << 16U;   /* BR bits: reset */
    CLK_PORT->BSRR = (uint32_t)CLK_PIN << 16U;
}
