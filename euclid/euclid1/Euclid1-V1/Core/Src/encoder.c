/**
  ******************************************************************************
  * @file    encoder.c
  * @brief   Detent-quantised reads from a hardware quadrature timer.
  ******************************************************************************
  */

#include "encoder.h"
#include "euclid_config.h"

void encoder_init(encoder_t *e, TIM_HandleTypeDef *htim, int8_t invert)
{
    e->htim           = htim;
    e->last_raw       = (uint16_t)__HAL_TIM_GET_COUNTER(htim);
    e->accum          = 0;
    e->invert         = invert ? -1 : 1;
    e->last_detent_ms = 0;
}

int32_t encoder_read(encoder_t *e)
{
    uint16_t raw = (uint16_t)__HAL_TIM_GET_COUNTER(e->htim);

    /* Unsigned subtraction wraps modulo 65536; reinterpreting the result as
       signed 16-bit recovers the true delta for any movement smaller than half
       the counter range. At 72 counts per revolution and a 1 ms poll, that is
       never in doubt. */
    int16_t diff = (int16_t)(uint16_t)(raw - e->last_raw);
    e->last_raw  = raw;

    e->accum += (int32_t)diff * e->invert;

    /* Truncating division toward zero keeps the remainder's sign consistent
       with the accumulator, so direction changes mid-detent don't drop counts. */
    int32_t detents = e->accum / ENC_COUNTS_PER_DETENT;
    e->accum       -= detents * ENC_COUNTS_PER_DETENT;

    return detents;
}

int32_t encoder_read_accel(encoder_t *e, uint32_t now_ms)
{
    int32_t d = encoder_read(e);
    if (d == 0) return 0;

    uint32_t dt = now_ms - e->last_detent_ms;
    e->last_detent_ms = now_ms;

    int32_t mult = 1;
    if      (dt <= ENC_ACCEL_FAST_MS) mult = ENC_ACCEL_FAST_MULT;
    else if (dt <= ENC_ACCEL_MED_MS)  mult = ENC_ACCEL_MED_MULT;

    return d * mult;
}
