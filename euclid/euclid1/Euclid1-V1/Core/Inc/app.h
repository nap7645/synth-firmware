/**
  ******************************************************************************
  * @file    app.h
  * @brief   Glue between CubeMX-generated init and the Euclid application.
  *
  * main.c should contain only two additions:
  *
  *     // USER CODE BEGIN 2
  *     app_init();
  *     // USER CODE END 2
  *
  *     while (1)
  *     {
  *       // USER CODE BEGIN 3
  *       app_tick();
  *     }
  *
  * Keeping it to that means regenerating from the .ioc can never destroy
  * application logic.
  ******************************************************************************
  */
#ifndef APP_H
#define APP_H

void app_init(void);
void app_tick(void);

#endif /* APP_H */
