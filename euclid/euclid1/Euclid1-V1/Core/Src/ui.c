/**
  ******************************************************************************
  * @file    ui.c
  * @brief   Front panel: five encoders, two buttons, Shift layer.
  ******************************************************************************
  */

#include "ui.h"
#include "encoder.h"
#include "seq_engine.h"
#include "euclid_config.h"

/* Both buttons sit behind a 1 k series resistor to GND with no RC network, so
   they read LOW when pressed and debouncing is entirely our problem. */
#define SS_PORT     GPIOB
#define SS_PIN      GPIO_PIN_0
#define SH_PORT     GPIOC
#define SH_PIN      GPIO_PIN_5

#define SAVE_INDICATE_MS    400U

/* -------------------------------------------------------------------------- */

typedef struct
{
    uint8_t  stable;        /* debounced level, 1 = pressed */
    uint8_t  raw_last;
    uint32_t raw_since;     /* when raw_last was first seen */
} button_t;

static encoder_t enc_tempo, enc_n, enc_k[SEQ_NUM_CHANNELS];

static button_t btn_ss, btn_sh;

static uint32_t last_poll_ms;

static uint8_t  combo_active;
static uint32_t combo_start_ms;
static uint8_t  combo_saved;        /* save already fired this hold */
static uint8_t  combo_consumed;     /* Shift was used to turn an encoder */

static param_result_t save_result = PARAM_OK;
static uint32_t       save_flash_until;

/* -------------------------------------------------------------------------- */

static uint8_t button_update(button_t *b, uint8_t raw, uint32_t now)
{
    if (raw != b->raw_last)
    {
        b->raw_last  = raw;
        b->raw_since = now;
    }
    else if (raw != b->stable && (now - b->raw_since) >= UI_DEBOUNCE_MS)
    {
        b->stable = raw;
    }
    return b->stable;
}

void ui_init(TIM_HandleTypeDef *tempo, TIM_HandleTypeDef *n,
             TIM_HandleTypeDef *k1,    TIM_HandleTypeDef *k2,
             TIM_HandleTypeDef *k3)
{
    HAL_TIM_Encoder_Start(tempo, TIM_CHANNEL_ALL);
    HAL_TIM_Encoder_Start(n,     TIM_CHANNEL_ALL);
    HAL_TIM_Encoder_Start(k1,    TIM_CHANNEL_ALL);
    HAL_TIM_Encoder_Start(k2,    TIM_CHANNEL_ALL);
    HAL_TIM_Encoder_Start(k3,    TIM_CHANNEL_ALL);

    encoder_init(&enc_tempo, tempo, 0);
    /* The N encoder's A leg lands on PA4 = TIM3_CH2, so it counts the opposite
       way to the other four. Flipped here rather than in the layout. */
    encoder_init(&enc_n,     n,     1);
    encoder_init(&enc_k[0],  k1,    0);
    encoder_init(&enc_k[1],  k2,    0);
    encoder_init(&enc_k[2],  k3,    0);

    uint32_t now = HAL_GetTick();

    btn_ss.stable = btn_ss.raw_last = 0U;
    btn_sh.stable = btn_sh.raw_last = 0U;
    btn_ss.raw_since = btn_sh.raw_since = now;

    last_poll_ms   = now;
    combo_active   = 0U;
    combo_saved    = 0U;
    combo_consumed = 0U;
    save_flash_until = 0U;
}

/* -------------------------------------------------------------------------- */

static void handle_encoders(uint8_t shift, uint32_t now)
{
    int32_t moved = 0;

    /* --- Tempo knob ------------------------------------------------------- */
    if (shift)
    {
        /* Gate length. Linear on purpose: 18 PPR x 5 % spans 5..95 % in exactly
           one revolution, and acceleration would make a knob you ride for
           performance behave differently depending on how fast you moved it. */
        int32_t d = encoder_read(&enc_tempo);
        if (d) { seq_adjust_gate(d); moved = 1; }
    }
    else
    {
        /* Tempo is the only control with acceleration -- 270 BPM at one per
           detent would be 15 full revolutions otherwise. */
        int32_t d = encoder_read_accel(&enc_tempo, now);
        if (d) { seq_adjust_tempo(d); }
    }

    /* --- N knob ----------------------------------------------------------- */
    {
        int32_t d = encoder_read(&enc_n);
        if (d)
        {
            if (shift) { seq_adjust_n_quantised(d); moved = 1; }
            else       { seq_adjust_n(d); }
        }
    }

    /* --- K knobs ---------------------------------------------------------- */
    for (uint8_t c = 0; c < SEQ_NUM_CHANNELS; c++)
    {
        int32_t d = encoder_read(&enc_k[c]);
        if (!d) continue;

        if (shift) { seq_adjust_offset(c, d); moved = 1; }
        else       { seq_adjust_k(c, d); }
    }

    /* If Shift was used to actually change something, releasing the button pair
       must not also toggle pause. */
    if (moved && combo_active) combo_consumed = 1U;
    if (moved && shift)        combo_consumed = 1U;
}

/* -------------------------------------------------------------------------- */

void ui_tick(void)
{
    uint32_t now = HAL_GetTick();
    if ((now - last_poll_ms) < UI_POLL_INTERVAL_MS) return;
    last_poll_ms = now;

    uint8_t raw_ss = (HAL_GPIO_ReadPin(SS_PORT, SS_PIN) == GPIO_PIN_RESET) ? 1U : 0U;
    uint8_t raw_sh = (HAL_GPIO_ReadPin(SH_PORT, SH_PIN) == GPIO_PIN_RESET) ? 1U : 0U;

    uint8_t ss_prev = btn_ss.stable;
    uint8_t sh_prev = btn_sh.stable;

    uint8_t ss = button_update(&btn_ss, raw_ss, now);
    uint8_t sh = button_update(&btn_sh, raw_sh, now);

    /* --- Start/Stop pressed ---------------------------------------------- */
    if (ss && !ss_prev)
    {
        if (sh)
        {
            /* Shift was already down: this is the combo gesture, not transport. */
            combo_active   = 1U;
            combo_start_ms = now;
            combo_saved    = 0U;
            combo_consumed = 0U;
        }
        else
        {
            seq_toggle();       /* stop resets to step 0 */
        }
    }

    /* --- combo held long enough to save ---------------------------------- */
    if (combo_active && !combo_saved && !combo_consumed &&
        (now - combo_start_ms) >= UI_SAVE_HOLD_MS)
    {
        /* A flash page erase stalls the bus for tens of milliseconds, which
           would be audible as clock jitter, so stop first. You asked for this
           with a deliberate two-second hold, so stopping is not a surprise. */
        if (seq.running) seq_stop();

        save_result = param_store_save(&seq);
        combo_saved = 1U;

        if (save_result == PARAM_OK) save_flash_until = now + SAVE_INDICATE_MS;
    }

    /* --- combo released --------------------------------------------------- */
    if (combo_active && (!ss || !sh))
    {
        if (!combo_saved && !combo_consumed) seq_pause_toggle();
        combo_active   = 0U;
        combo_saved    = 0U;
        combo_consumed = 0U;
    }

    (void)sh_prev;

    handle_encoders(sh, now);
}

param_result_t ui_last_save_result(void)
{
    return save_result;
}

uint8_t ui_save_indicator(void)
{
    return (HAL_GetTick() < save_flash_until) ? 1U : 0U;
}

uint8_t ui_shift_held(void)
{
    return btn_sh.stable;
}
