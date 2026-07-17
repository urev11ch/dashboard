"""Очистка окружения от меток бутлоадера PyInstaller перед запуском установщика.

Регрессия: onefile-приложение помечает окружение `_PYI_APPLICATION_HOME_DIR`
(путь распаковки в %TEMP%\\_MEIxxxxxx) и `_PYI_PARENT_PROCESS_LEVEL`. Метки
наследовались по цепочке приложение → установщик → перезапущенное приложение,
и новый экземпляр лез за python3xx.dll в каталог уже вышедшего родителя:
«Failed to load Python DLL ...\\_MEI151282\\python312.dll».
"""
import run_wash_desktop as desktop


def test_strips_pyinstaller_markers(monkeypatch):
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", r"C:\Users\U\AppData\Local\Temp\_MEI151282")
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", r"C:\Program Files\App\App.exe")
    monkeypatch.setenv("_PYI_SPLASH_IPC", "12345")
    monkeypatch.setenv("_PYI_LINUX_PROCESS_NAME", "app")
    monkeypatch.setenv("_MEIPASS2", r"C:\Temp\_MEI999")

    env = desktop._clean_pyinstaller_env()

    assert not [key for key in env if key.startswith("_PYI_")]
    assert "_MEIPASS2" not in env


def test_keeps_the_rest_of_environment(monkeypatch):
    # Установщику нужно нормальное окружение: срезаем только метки бутлоадера.
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", r"C:\Temp\_MEI1")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\U\AppData\Local")
    monkeypatch.setenv("PATH", r"C:\Windows\system32")

    env = desktop._clean_pyinstaller_env()

    assert env["LOCALAPPDATA"] == r"C:\Users\U\AppData\Local"
    assert env["PATH"] == r"C:\Windows\system32"
    assert "_PYI_APPLICATION_HOME_DIR" not in env


def test_does_not_mutate_real_environment(monkeypatch):
    # os.environ.copy() — не правим окружение самого приложения: оно ещё живёт
    # 1.5 с до закрытия окна и его собственный бутлоадер этими метками пользуется.
    import os

    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", r"C:\Temp\_MEI1")
    desktop._clean_pyinstaller_env()
    assert os.environ.get("_PYI_APPLICATION_HOME_DIR") == r"C:\Temp\_MEI1"
