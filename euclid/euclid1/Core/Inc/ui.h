/**
  ******************************************************************************
  * @file    ui.h
  * @brief   Front panel: five encoders, two buttons, Shift layer.
  *
  * Control map
  * -----------
  *              plain                    with Shift held
  *   Tempo      tempo, 30..300 BPM       global gate length, 5..95 %
  *   N          bank length, 1..64       length snapped to 1/2/4/8/16/32/64
  *   K1         k for channel 1          offset (rotation) for channel 1
  *   K2         k for channel 2          offset for channel 2
  *   K3         k for channel 3          offset for channel 3
  *
  *   Start/Stop            start, or stop and reset to step 0
  *   Shift + Start/Stop    tap:      pause / resume in place
  *                         hold 2 s: save parameters to flash
  *
  * Gesture order matters: press Shift FIRST, then Start/Stop. Pressing
  * Start/Stop first acts on the transport immediately, as a transport button
  * should, and adding Shift afterwards will not retroactively cancel it.
  ******************************************************************************
  */
#ifndef UI_H
#define UI_H

#include "main.h"
#include "param_store.h"
#include <stdint.h>

void ui_init(TIM_HandleTypeDef *tempo, TIM_HandleTypeDef *n,
             TIM_HandleTypeDef *k1,    TIM_HandleTypeDef *k2,
             TIM_HandleTypeDef *k3);

/**
  * @brief  Poll buttons and encoders. Call from the main loop; it rate-limits
  *         itself to UI_POLL_INTERVAL_MS internally.
  */
void ui_tick(void);

/** @brief  Result of the most recent save attempt, for debug reporting. */
param_result_t ui_last_save_result(void);

/** @brief  Non-zero for a short while after a save completes. Hook a panel LED
  *         here on Euclid 3; there is nothing safe to blink on Euclid 1. */
uint8_t ui_save_indicator(void);

#endif /* UI_H */
