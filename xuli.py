from machine import Pin, ADC, I2C, time_pulse_us
from i2c_lcd import I2cLcd
import time

# --- CẤU HÌNH CHÂN KẾT NỐI ---
PH_PIN         = 34
TRIG_PIN       = 5
ECHO_PIN       = 15

# Ngưỡng mức nước (cm)
DISTANCE_EMPTY = 10.5  # Bơm chuyển ngắt khi khoảng cách >= 10.5 cm

# LED chỉ thị trạng thái (Đã bỏ Buzzer)
LED_RED        = 16    # Axit (< 6.0)
LED_GREEN      = 17    # Chuẩn (6.0 - 8.0)
LED_YELLOW     = 18    # Bazơ (> 8.0)

# Module Relay 4 kênh (Active LOW)
RELAY_ACID     = 25    # Kênh 1: Bơm Axit
RELAY_BASE     = 26    # Kênh 2: Bơm Bazơ
RELAY_OUT      = 27    # Kênh 3: Bơm chuyển nước
RELAY_MIXER    = 23    # Kênh 4: Motor khuấy 12V

RELAY_ON       = 0     # LOW
RELAY_OFF      = 1     # HIGH

NUM_READINGS   = 5
TIME_SETTLE    = 2000  # Chờ nước phẳng lặng triệt tiêu EMI: 2 giây
TIME_MIX_EXTRA = 4000  # Giữ motor khuấy thêm sau khi châm: 4 giây

# --- KHỞI TẠO PHẦN CỨNG ---
i2c = I2C(0, sda=Pin(21), scl=Pin(22), freq=100000)
lcd = I2cLcd(i2c, 0x27, 2, 16)

# Hệ số hiệu chuẩn nguồn 12V thực tế
slope = -5.1546
intercept = 20.4492

# Chân LED
led_red = Pin(LED_RED, Pin.OUT)
led_green = Pin(LED_GREEN, Pin.OUT)
led_yellow = Pin(LED_YELLOW, Pin.OUT)

# Chân Relay
relay_acid = Pin(RELAY_ACID, Pin.OUT)
relay_base = Pin(RELAY_BASE, Pin.OUT)
relay_out = Pin(RELAY_OUT, Pin.OUT)
relay_mixer = Pin(RELAY_MIXER, Pin.OUT)

# Chân HC-SR04
trig = Pin(TRIG_PIN, Pin.OUT)
echo = Pin(ECHO_PIN, Pin.IN)

# Cấu hình ADC ESP32
adc = ADC(Pin(PH_PIN))
adc.width(ADC.WIDTH_12BIT)
adc.atten(ADC.ATTN_11DB)


# Hàm tắt toàn bộ các rơ-le
def stopAllActuators():
    relay_acid.value(RELAY_OFF)
    relay_base.value(RELAY_OFF)
    relay_out.value(RELAY_OFF)
    relay_mixer.value(RELAY_OFF)


# Cập nhật LED theo giá trị pH
def updateLEDs(ph):
    if ph < 6.0:
        led_red.value(1)
        led_green.value(0)
        led_yellow.value(0)
    elif ph > 8.0:
        led_red.value(0)
        led_green.value(0)
        led_yellow.value(1)
    else:
        led_red.value(0)
        led_green.value(1)
        led_yellow.value(0)


# Đọc khoảng cách từ HC-SR04 (cm)
def readDistanceCm():
    trig.value(0)
    time.sleep_us(2)
    trig.value(1)
    time.sleep_us(10)
    trig.value(0)
    duration = time_pulse_us(echo, 1, 25000)  # Timeout 25ms
    if duration <= 0:
        return -1.0
    return (duration * 0.0343) / 2.0


# Đọc 5 mẫu analog và trích xuất giá trị trung vị
def getMedianOf5():
    phSamples = [0.0] * NUM_READINGS
    voltSamples = [0.0] * NUM_READINGS

    for i in range(NUM_READINGS):
        rawAdc = adc.read()
        voltage = (rawAdc / 4095.0) * 3.3
        ph = (slope * voltage) + intercept
        if ph < 0.0:
            ph = 0.0
        if ph > 14.0:
            ph = 14.0
        voltSamples[i] = voltage
        phSamples[i] = ph
        time.sleep_ms(20)

    phSamples.sort()
    voltSamples.sort()

    medianPh = phSamples[2]
medianVolt = voltSamples[2]
    return medianPh, medianVolt


# Bộ điều khiển Fuzzy Logic: Tính toán thời gian châm xung mờ thích nghi (ms)
# Trả về (thời gian xung ms, loại hành động)
def computeFuzzyPulse(phVal):
    e = phVal - 7.0

    if phVal >= 6.0 and phVal <= 8.0:
        return 0, "BALANCED"

    # Axit -> Châm Bazơ
    if e < 0:
        actionType = "DOSE_BASE"
        absE = abs(e)
        if absE >= 3.0:
            return 4000, actionType   # pH <= 4.0: Xung 4.0s
        elif absE >= 2.0:
            return 2500, actionType   # 4.0 < pH <= 5.0: Xung 2.5s
        elif absE >= 1.0:
            return 1200, actionType   # 5.0 < pH < 6.0: Xung 1.2s
        else:
            return 400, actionType    # Tiệm cận chuẩn: Vi châm 0.4s

    # Bazơ -> Châm Axit
    if e > 0:
        actionType = "DOSE_ACID"
        if e >= 3.0:
            return 4000, actionType   # pH >= 10.0: Xung 4.0s
        elif e >= 2.0:
            return 2500, actionType   # 9.0 <= pH < 10.0: Xung 2.5s
        elif e >= 1.0:
            return 1200, actionType   # 8.0 < pH < 9.0: Xung 1.2s
        else:
            return 400, actionType    # Tiệm cận chuẩn: Vi châm 0.4s

    return 0, ""


def setup():
    # Cấu hình LED
    led_red.value(0)
    led_green.value(0)
    led_yellow.value(0)

    # Cấu hình Relay
    stopAllActuators()

    # Khởi tạo LCD
    lcd.backlight_on()
    lcd.move_to(0, 0)
    lcd.putstr("pH AUTO SYSTEM  ")
    lcd.move_to(0, 1)
    lcd.putstr("FUZZY CLOSEDLOOP")
    time.sleep_ms(1500)
    lcd.clear()


def loop():
    # ==============================================================
    # BƯỚC 1: TẮT HẾT TẢI, CHỜ 2 GIÂY CHO NƯỚC PHẲNG LẶNG VÀ TRIỆT NHIỄU EMI
    # ==============================================================
    stopAllActuators()

    for t in range(TIME_SETTLE // 1000, 0, -1):
        lcd.move_to(0, 1)
        lcd.putstr("SETTLING: ")
        lcd.putstr(str(t))
        lcd.putstr("s     ")
        time.sleep_ms(1000)

    # ==============================================================
    # BƯỚC 2: TIẾN HÀNH ĐO pH (2 ĐỢT TRUNG VỊ 5 MẪU - NGUYÊN BẢN)
    # ==============================================================
    lcd.move_to(0, 1)
    lcd.putstr("SAMPLING pH...  ")

    batch1_ph, batch1_volt = getMedianOf5()
    time.sleep_ms(40)
    batch2_ph, batch2_volt = getMedianOf5()

    currentPh = (batch1_ph + batch2_ph) / 2.0
    currentVolt = (batch1_volt + batch2_volt) / 2.0
    currentDist = readDistanceCm()

    updateLEDs(currentPh)

    # Hiển thị LCD Hàng 0
    lcd.move_to(0, 0)
    lcd.putstr("pH:")
    lcd.putstr("{:.2f}".format(currentPh))
    lcd.putstr(" ")

    print("pH: " + "{:.2f}".format(currentPh)
          + " | Volt: " + "{:.3f}".format(currentVolt)
          + "V | Dist: " + "{:.1f}".format(currentDist) + "cm")
# ==============================================================
    # BƯỚC 3: TÍNH TOÁN XUNG VÀ THỰC THI ĐIỀU KHIỂN VÒNG KÍN FUZZY
    # ==============================================================
    pulseMs, action = computeFuzzyPulse(currentPh)

    if action == "DOSE_BASE":
        # Dung dịch Axit -> Bật Bơm Bazơ + Motor Khuấy
        lcd.putstr("[FZ-BASE]")
        lcd.move_to(0, 1)
        lcd.putstr("BASE:")
        lcd.putstr(str(pulseMs))
        lcd.putstr("ms+MIX ")

        relay_base.value(RELAY_ON)
        relay_mixer.value(RELAY_ON)
        time.sleep_ms(pulseMs)  # Châm đúng xung mờ

        # Tắt bơm châm, tiếp tục khuấy thêm 4 giây để hòa tan
        relay_base.value(RELAY_OFF)
        lcd.move_to(0, 1)
        lcd.putstr("MIXING EXTRA 4s ")
        time.sleep_ms(TIME_MIX_EXTRA)

        relay_mixer.value(RELAY_OFF)

    elif action == "DOSE_ACID":
        # Dung dịch Bazơ -> Bật Bơm Axit + Motor Khuấy
        lcd.putstr("[FZ-ACID]")
        lcd.move_to(0, 1)
        lcd.putstr("ACID:")
        lcd.putstr(str(pulseMs))
        lcd.putstr("ms+MIX ")

        relay_acid.value(RELAY_ON)
        relay_mixer.value(RELAY_ON)
        time.sleep_ms(pulseMs)  # Châm đúng xung mờ

        # Tắt bơm châm, tiếp tục khuấy thêm 4 giây để hòa tan
        relay_acid.value(RELAY_OFF)
        lcd.move_to(0, 1)
        lcd.putstr("MIXING EXTRA 4s ")
        time.sleep_ms(TIME_MIX_EXTRA)

        relay_mixer.value(RELAY_OFF)

    else:
        # Dung dịch đạt chuẩn trung hòa (6.0 - 8.0)
        lcd.putstr("[CHUAN]")
        currentDist = readDistanceCm()

        # Nếu bồn còn nước (khoảng cách chưa chạm 10.5cm)
        if currentDist < DISTANCE_EMPTY and currentDist > 0:
            print(">> Nuoc dat chuan! Bat bom chuyen sang be moi...")
            relay_out.value(RELAY_ON)

            # Khóa đo pH, vòng lặp chỉ theo dõi khoảng cách cạn bể
            while True:
                currentDist = readDistanceCm()

                lcd.move_to(0, 1)
                lcd.putstr("DRAIN D:")
                if currentDist > 0:
                    lcd.putstr("{:.1f}".format(currentDist))
                    lcd.putstr("cm   ")
                else:
                    lcd.putstr("WAIT... ")

                # Đạt ngưỡng cạn 10.5cm -> Tự động dừng bơm
                if currentDist >= DISTANCE_EMPTY and currentDist > 0:
                    stopAllActuators()
                    lcd.move_to(0, 1)
                    lcd.putstr("DRAIN FINISHED! ")
                    print(">> Be da can (>= 10.5cm)! Dung bom chuyen.")
                    time.sleep_ms(2000)
                    break  # Thoát vòng lặp, quay lại Bước 1 để lắng nước

                time.sleep_ms(300)
        else:
            lcd.move_to(0, 1)
            lcd.putstr("TANK EMPTY! OK  ")
            time.sleep_ms(1500)


setup()
while True:
loop()
