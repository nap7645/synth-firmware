/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.h
  * @brief          : Header for main.c file.
  *                   This file contains the common defines of the application.
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */

/* Define to prevent recursive inclusion -------------------------------------*/
#ifndef __MAIN_H
#define __MAIN_H

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ------------------------------------------------------------------*/
#include "stm32g4xx_hal.h"

#include "stm32g4xx_nucleo.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */

/* USER CODE END Includes */

/* Exported types ------------------------------------------------------------*/
/* USER CODE BEGIN ET */
/* Oscillator waveform selection. Order is part of the serial protocol:
 * the host sends w0..w3, so do not renumber these. */
typedef enum {
  WAVE_SINE = 0,
  WAVE_SAW,
  WAVE_SQUARE,
  WAVE_TRI,
  WAVE_COUNT
} wave_t;
/* USER CODE END ET */

/* Exported constants --------------------------------------------------------*/
/* USER CODE BEGIN EC */

/* USER CODE END EC */

/* Exported macro ------------------------------------------------------------*/
/* USER CODE BEGIN EM */

/* USER CODE END EM */

void HAL_TIM_MspPostInit(TIM_HandleTypeDef *htim);

/* Exported functions prototypes ---------------------------------------------*/
void Error_Handler(void);

/* USER CODE BEGIN EFP */

/* USER CODE END EFP */

/* Private defines -----------------------------------------------------------*/
#define T_SWDIO_Pin GPIO_PIN_13
#define T_SWDIO_GPIO_Port GPIOA
#define T_SWCLK_Pin GPIO_PIN_14
#define T_SWCLK_GPIO_Port GPIOA
#define T_SWO_Pin GPIO_PIN_3
#define T_SWO_GPIO_Port GPIOB

/* USER CODE BEGIN Private defines */
/* ---- Board pins ------------------------------------------------------ */
#define LD2_PIN                 GPIO_PIN_8
#define LD2_GPIO_PORT           GPIOB
#define VCO_PWM_CHANNEL         TIM_CHANNEL_1

/* ---- Signal chain ----------------------------------------------------- */
/* PWM carrier = 170 MHz / (PWM_ARR+1) = 170 kHz, 1000 duty steps (~10 bit).
 * Sample rate = 170 MHz / 4000 = 42.5 kHz, exactly the carrier / 4 so the
 * two clocks do not beat. These MUST match TIM2/TIM6 in CubeMX: if you
 * change Counter Period there, change it here too. */
#define PWM_ARR                 999U
#define SAMPLE_RATE_HZ          42500UL

/* Scope telemetry: FRAME_SAMPLES consecutive samples, FRAME_RATE_HZ times
 * per second, streamed over the ST-LINK VCP. */
#define FRAME_SAMPLES           128U
#define FRAME_RATE_HZ           15U
/* USER CODE END Private defines */

#ifdef __cplusplus
}
#endif

#endif /* __MAIN_H */
