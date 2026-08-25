/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
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
/* Includes ------------------------------------------------------------------*/
#include "main.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "osc.h"
#include <string.h>
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define FRAME_PERIOD_TICKS (SAMPLE_RATE_HZ / FRAME_RATE_HZ)
#define LED_PERIOD_TICKS   (SAMPLE_RATE_HZ / 2U)   /* 0.5 s -> 1 Hz blink */
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

UART_HandleTypeDef hlpuart1;

TIM_HandleTypeDef htim2;
TIM_HandleTypeDef htim6;

/* USER CODE BEGIN PV */
static osc_t osc;

/* Scope capture. Ping-pong buffers so the audio ISR never writes the buffer
 * the main loop is busy formatting. */
static uint16_t cap_buf[2][FRAME_SAMPLES];
static volatile uint8_t  cap_wr      = 0;   /* buffer the ISR fills        */
static volatile uint8_t  cap_ready   = 0;   /* a full frame is waiting     */
static volatile uint8_t  cap_ready_b = 0;   /* which buffer holds it       */
static volatile uint16_t cap_idx     = 0;
static volatile uint8_t  cap_state   = 0;   /* 0 idle, 1 armed, 2 running  */
static volatile uint32_t tick        = 0;

/* VCP transmit / receive */
static char txbuf[FRAME_SAMPLES * 5U + 64U];
static volatile uint8_t tx_busy = 0;
static uint8_t rx_byte;
static char    rx_line[32];
static uint8_t rx_len = 0;
static volatile uint8_t line_ready = 0;
static char    line_buf[32];
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_TIM2_Init(void);
static void MX_TIM6_Init(void);
static void MX_LPUART1_UART_Init(void);
/* USER CODE BEGIN PFP */
static void vcp_send(const char *s);
static void send_state(void);
static void handle_line(const char *s);
/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
/* Blocking only while a previous transmit drains — never called from the
 * audio ISR, so this cannot stall the oscillator. */
static void vcp_send(const char *s)
{
  while (tx_busy) { }
  size_t n = strlen(s);
  if (n >= sizeof(txbuf)) n = sizeof(txbuf) - 1U;
  memcpy(txbuf, s, n);
  tx_busy = 1;
  HAL_UART_Transmit_IT(&hlpuart1, (uint8_t *)txbuf, (uint16_t)n);
}

static uint32_t parse_uint(const char *s)
{
  uint32_t v = 0;
  while (*s >= '0' && *s <= '9') { v = v * 10U + (uint32_t)(*s - '0'); s++; }
  return v;
}

static const char *wave_name(uint8_t w)
{
  switch (w) {
    case WAVE_SINE:   return "sine";
    case WAVE_SAW:    return "saw";
    case WAVE_SQUARE: return "square";
    case WAVE_TRI:    return "tri";
    default:          return "?";
  }
}

/* "S <freq> <waveidx> <name> <amp> <samplerate> <outmax>"
 * tools/scope.py parses this positionally — keep the field order stable. */
static void send_state(void)
{
  char b[80];
  char *p = b;
  const char *w = wave_name(osc.wave);
  uint32_t f = osc_get_freq(&osc);

  *p++ = 'S'; *p++ = ' ';
  { char t[12]; int8_t i = 0; if (!f) t[i++] = '0';
    while (f) { t[i++] = (char)('0' + f % 10U); f /= 10U; }
    while (i) *p++ = t[--i]; }
  *p++ = ' '; *p++ = (char)('0' + osc.wave); *p++ = ' ';
  while (*w) *p++ = *w++;
  *p++ = ' ';
  { uint32_t a = osc.amp_pct; char t[6]; int8_t i = 0; if (!a) t[i++] = '0';
    while (a) { t[i++] = (char)('0' + a % 10U); a /= 10U; }
    while (i) *p++ = t[--i]; }
  *p++ = ' ';
  { uint32_t sr = SAMPLE_RATE_HZ; char t[12]; int8_t i = 0;
    while (sr) { t[i++] = (char)('0' + sr % 10U); sr /= 10U; }
    while (i) *p++ = t[--i]; }
  *p++ = ' ';
  { uint32_t om = PWM_ARR; char t[8]; int8_t i = 0; if (!om) t[i++] = '0';
    while (om) { t[i++] = (char)('0' + om % 10U); om /= 10U; }
    while (i) *p++ = t[--i]; }
  *p++ = '\r'; *p++ = '\n'; *p = '\0';
  vcp_send(b);
}

/* One command per line: f<hz>  w<0-3>  a<0-100>  ? */
static void handle_line(const char *s)
{
  switch (s[0]) {
    case 'f': osc_set_freq(&osc, parse_uint(s + 1)); break;
    case 'w': { uint32_t w = parse_uint(s + 1);
                if (w < WAVE_COUNT) osc_set_wave(&osc, (uint8_t)w); } break;
    case 'a': osc_set_amp(&osc, (uint8_t)parse_uint(s + 1)); break;
    case '?': break;
    default:  vcp_send("# ? unknown cmd\r\n"); return;
  }
  send_state();
}
/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_TIM2_Init();
  MX_TIM6_Init();
  MX_LPUART1_UART_Init();
  /* USER CODE BEGIN 2 */

  osc_init(&osc, SAMPLE_RATE_HZ, PWM_ARR);

  HAL_TIM_PWM_Start(&htim2, VCO_PWM_CHANNEL);
  HAL_UART_Receive_IT(&hlpuart1, &rx_byte, 1);
  HAL_TIM_Base_Start_IT(&htim6);   /* starts the 42.5 kHz audio tick */

  vcp_send("\r\n# VCO firmware up. NUCLEO-G431KB, 42500 Hz, PWM 170 kHz on PA0.\r\n");
  send_state();
  /* USER CODE END 2 */

  /* Initialize leds */
  BSP_LED_Init(LED_GREEN);

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {

    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */

    if (line_ready) {
      line_ready = 0;
      handle_line(line_buf);
    }

    if (cap_ready && !tx_busy) {
      uint8_t b = cap_ready_b;
      cap_ready = 0;

      /* Hand-rolled formatting: snprintf per sample is far too slow and
       * drags a large chunk of newlib into flash. "W <n> s0 s1 ... sn-1". */
      char *p = txbuf;
      *p++ = 'W'; *p++ = ' ';
      uint16_t n = FRAME_SAMPLES;
      if (n >= 100) { *p++ = (char)('0' + n / 100); }
      if (n >= 10)  { *p++ = (char)('0' + (n / 10) % 10); }
      *p++ = (char)('0' + n % 10);

      for (uint16_t i = 0; i < FRAME_SAMPLES; i++) {
        uint16_t v = cap_buf[b][i];
        *p++ = ' ';
        if (v >= 100) *p++ = (char)('0' + v / 100);
        if (v >= 10)  *p++ = (char)('0' + (v / 10) % 10);
        *p++ = (char)('0' + v % 10);
      }
      *p++ = '\r'; *p++ = '\n';

      tx_busy = 1;
      HAL_UART_Transmit_IT(&hlpuart1, (uint8_t *)txbuf, (uint16_t)(p - txbuf));
    }
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  HAL_PWREx_ControlVoltageScaling(PWR_REGULATOR_VOLTAGE_SCALE1_BOOST);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSI;
  RCC_OscInitStruct.PLL.PLLM = RCC_PLLM_DIV4;
  RCC_OscInitStruct.PLL.PLLN = 85;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
  RCC_OscInitStruct.PLL.PLLQ = RCC_PLLQ_DIV2;
  RCC_OscInitStruct.PLL.PLLR = RCC_PLLR_DIV2;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV1;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_4) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief LPUART1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_LPUART1_UART_Init(void)
{

  /* USER CODE BEGIN LPUART1_Init 0 */

  /* USER CODE END LPUART1_Init 0 */

  /* USER CODE BEGIN LPUART1_Init 1 */

  /* USER CODE END LPUART1_Init 1 */
  hlpuart1.Instance = LPUART1;
  hlpuart1.Init.BaudRate = 115200;
  hlpuart1.Init.WordLength = UART_WORDLENGTH_8B;
  hlpuart1.Init.StopBits = UART_STOPBITS_1;
  hlpuart1.Init.Parity = UART_PARITY_NONE;
  hlpuart1.Init.Mode = UART_MODE_TX_RX;
  hlpuart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  hlpuart1.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
  hlpuart1.Init.ClockPrescaler = UART_PRESCALER_DIV1;
  hlpuart1.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;
  if (HAL_UART_Init(&hlpuart1) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_SetTxFifoThreshold(&hlpuart1, UART_TXFIFO_THRESHOLD_1_8) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_SetRxFifoThreshold(&hlpuart1, UART_RXFIFO_THRESHOLD_1_8) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_DisableFifoMode(&hlpuart1) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN LPUART1_Init 2 */

  /* USER CODE END LPUART1_Init 2 */

}

/**
  * @brief TIM2 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM2_Init(void)
{

  /* USER CODE BEGIN TIM2_Init 0 */

  /* USER CODE END TIM2_Init 0 */

  TIM_ClockConfigTypeDef sClockSourceConfig = {0};
  TIM_MasterConfigTypeDef sMasterConfig = {0};
  TIM_OC_InitTypeDef sConfigOC = {0};

  /* USER CODE BEGIN TIM2_Init 1 */

  /* USER CODE END TIM2_Init 1 */
  htim2.Instance = TIM2;
  htim2.Init.Prescaler = 0;
  htim2.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim2.Init.Period = 999;
  htim2.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim2.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;
  if (HAL_TIM_Base_Init(&htim2) != HAL_OK)
  {
    Error_Handler();
  }
  sClockSourceConfig.ClockSource = TIM_CLOCKSOURCE_INTERNAL;
  if (HAL_TIM_ConfigClockSource(&htim2, &sClockSourceConfig) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_Init(&htim2) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_RESET;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim2, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sConfigOC.OCMode = TIM_OCMODE_PWM1;
  sConfigOC.Pulse = 499;
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
  if (HAL_TIM_PWM_ConfigChannel(&htim2, &sConfigOC, TIM_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM2_Init 2 */

  /* USER CODE END TIM2_Init 2 */
  HAL_TIM_MspPostInit(&htim2);

}

/**
  * @brief TIM6 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM6_Init(void)
{

  /* USER CODE BEGIN TIM6_Init 0 */

  /* USER CODE END TIM6_Init 0 */

  TIM_MasterConfigTypeDef sMasterConfig = {0};

  /* USER CODE BEGIN TIM6_Init 1 */

  /* USER CODE END TIM6_Init 1 */
  htim6.Instance = TIM6;
  htim6.Init.Prescaler = 0;
  htim6.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim6.Init.Period = 3999;
  htim6.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;
  if (HAL_TIM_Base_Init(&htim6) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_RESET;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim6, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM6_Init 2 */

  /* USER CODE END TIM6_Init 2 */

}

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};
  /* USER CODE BEGIN MX_GPIO_Init_1 */

  /* USER CODE END MX_GPIO_Init_1 */

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(GPIOB, GPIO_PIN_8, GPIO_PIN_RESET);

  /*Configure GPIO pin : PB8 */
  GPIO_InitStruct.Pin = GPIO_PIN_8;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  /* USER CODE BEGIN MX_GPIO_Init_2 */

  /* USER CODE END MX_GPIO_Init_2 */
}

/* USER CODE BEGIN 4 */
/* ---- audio ISR: fires at SAMPLE_RATE_HZ from TIM6 ---------------------- */
void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
  if (htim->Instance != TIM6) return;

  uint16_t sample = osc_next(&osc);
  __HAL_TIM_SET_COMPARE(&htim2, VCO_PWM_CHANNEL, sample);

  tick++;
  if ((tick % LED_PERIOD_TICKS) == 0U) {
    HAL_GPIO_TogglePin(LD2_GPIO_PORT, LD2_PIN);
  }
  /* Arm a capture on the frame interval, but only actually start it on the
   * next phase wrap — that trigger is what makes the host-side trace stand
   * still instead of sliding across the screen. */
  if ((tick % FRAME_PERIOD_TICKS) == 0U && cap_state == 0U && !cap_ready) {
    cap_state = 1;
  }
  if (cap_state == 1U && osc.wrapped) {
    cap_state = 2;
    cap_idx = 0;
  }
  if (cap_state == 2U) {
    cap_buf[cap_wr][cap_idx++] = sample;
    if (cap_idx >= FRAME_SAMPLES) {
      cap_state   = 0;
      cap_ready_b = cap_wr;
      cap_ready   = 1;
      cap_wr     ^= 1U;
    }
  }
}

/* ---- VCP callbacks ----------------------------------------------------- */
void HAL_UART_TxCpltCallback(UART_HandleTypeDef *huart)
{
  if (huart->Instance == LPUART1) tx_busy = 0;
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
  if (huart->Instance != LPUART1) return;
  char ch = (char)rx_byte;
  if (ch == '\n' || ch == '\r') {
    if (rx_len > 0U && !line_ready) {
      rx_line[rx_len] = '\0';
      memcpy(line_buf, rx_line, rx_len + 1U);
      line_ready = 1;
    }
    rx_len = 0;
  } else if (rx_len < sizeof(rx_line) - 1U) {
    rx_line[rx_len++] = ch;
  }
  HAL_UART_Receive_IT(&hlpuart1, &rx_byte, 1);
}
/* USER CODE END 4 */

/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * File Name          :
  * Description        :
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

/**
  * @}
  */

/**
  * @}
  */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
