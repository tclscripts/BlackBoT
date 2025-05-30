import subprocess
import sys

try:
    import pyowm
except ImportError:
    print("pyowm not found. Installing pyowm...")
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pyowm'])
    import pyowm

from pyowm.utils import config
from pyowm.utils import timestamps


def get_weather(city, api_key):
    owm = pyowm.OWM(api_key)
    mgr = owm.weather_manager()

    observation = mgr.weather_at_place(city)
    weather = observation.weather

    temperature = weather.temperature('celsius')['temp']
    status = weather.detailed_status
    humidity = weather.humidity
    wind_speed = weather.wind()['speed']
    pressure = weather.pressure['press']

    return {
        'city': city,
        'temperature': temperature,
        'description': status,
        'humidity': humidity,
        'pressure': pressure,
        'wind_speed': wind_speed
    }


if __name__ == "__main__":
    api_key = "XXXXXXX"
    city = input("Introduceți numele orașului: ")
    weather = get_weather(city, api_key)

    if weather:
        print(f"Vremea în {weather['city']}:")
        print(f"Temperatura: {weather['temperature']}°C")
        print(f"Descriere: {weather['description']}")
        print(f"Umiditate: {weather['humidity']}%")
        print(f"Presiune: {weather['pressure']} hPa")
        print(f"Viteza vântului: {weather['wind_speed']} m/s")
    else:
        print("Nu am putut prelua datele meteo pentru orașul specificat.")
