/**
  ******************************************************************************
  * @file    encoder.h
  * @brief   Detent-quantised reads from a hardware quadrature timer.
  ******************************************************************************
  */
#ifndef ENCODER_H
#define ENCODER_H

#include "main.h"
#include <stdint.h>

typedef struct
{
    TIM_HandleTypeDef *htim;
    uint16_t           last_raw;        /* raw counter at previous read */
    int32_t            accum;           /* sub-detent quadrature remainder */
    int8_t             invert;          /* -1 to flip direction, +1 normal */
    uint32_t           last_detent_ms;  /* for velocity */
} encoder_t;

/**
  * @brief  Bind an encoder to a timer already running in encoder mode.
  * @param  invert  pass 1 to reverse direction. Needed for the N encoder: the
  *                 schematic puts its A leg on PA4 which is TIM3_CH2, so it
  *                 counts backwards relative to the other four.
  */
void encoder_init(encoder_t *e, TIM_HandleTypeDef *htim, int8_t invert);

/**
  * @brief  Signed detents accumulated since the previous call. Linear, no
  *         acceleration. Use for N, k, offset and gate length.
  *
  * Wrap-safe: the counter free-runs over the full 16-bit range and the
  * difference is taken as a signed 16-bit value, so a wrap from 65535 to 0
  * reads as +1 rather than -65535.
  */
int32_t encoder_read(encoder_t *e);

/**
  * @brief  As encoder_read(), but multiplied up when turned fast.
  *         Use for tempo only.
  * @param  now_ms  current HAL_GetTick()
  */
int32_t encoder_read_accel(encoder_t *e, uint32_t now_ms);

#endif /* ENCODER_H */
