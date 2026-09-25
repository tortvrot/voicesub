# -*- coding: utf-8 -*-
"""
Запусти этот файл ОДИН РАЗ (нужен интернет), чтобы скачать языковые пакеты
для офлайн-перевода.

ПОЧЕМУ V2 ЛУЧШЕ V1:
Раньше мы пытались поставить пакет "китайский -> русский" напрямую — а такого
пакета просто не существует в природе. В v2 мы ставим пакеты через английский
как "мост": китайский -> английский, и отдельно английский -> русский.
argos-translate сам умеет комбинировать два таких пакета в один перевод
(китайский -> английский -> русский) — для этого ничего дополнительно
настраивать не нужно, просто чтобы оба пакета были установлены.

Список языков ниже покрывает большинство игровых серверов: английский,
украинский, немецкий, французский, испанский, польский, китайский,
японский, корейский, португальский, итальянский, турецкий.
Если нужен ещё какой-то язык — допиши код в список SOURCE_LANGS.
Коды языков: en, ru, uk, de, fr, es, pl, zh, ja, ko, pt, it, tr, ar, hi и др.
"""

import argostranslate.package
import argostranslate.translate

TARGET_LANG = "ru"  # язык, на который переводим (поменяй, если нужен другой)
SOURCE_LANGS = ["en", "uk", "de", "fr", "es", "pl", "zh", "ja", "ko", "pt", "it", "tr"]

print("Обновляю список доступных пакетов перевода...")
argostranslate.package.update_package_index()
available_packages = argostranslate.package.get_available_packages()


def install_pair(src, dst):
    match = next(
        (p for p in available_packages if p.from_code == src and p.to_code == dst),
        None,
    )
    if match is None:
        print(f"  Пропускаю {src} -> {dst}: пакет не найден в источнике.")
        return False
    print(f"  Скачиваю пакет {src} -> {dst} ...")
    download_path = match.download()
    argostranslate.package.install_from_path(download_path)
    return True


# 1. Ставим "мост" — английский -> целевой язык (если целевой не сам английский)
if TARGET_LANG != "en":
    print(f"Устанавливаю мост en -> {TARGET_LANG} ...")
    install_pair("en", TARGET_LANG)

# 2. Для каждого исходного языка ставим либо прямой пакет src -> target,
#    либо, если такого нет, пакет src -> en (дальше сработает мост через en)
for src in SOURCE_LANGS:
    if src == TARGET_LANG:
        continue
    print(f"\nЯзык: {src}")
    direct_ok = install_pair(src, TARGET_LANG)
    if not direct_ok and src != "en":
        install_pair(src, "en")

print("\nГотово! Языковые пакеты установлены. Теперь можно запускать main.py")
print("Если какой-то язык всё равно не переводится — пришли код языка,")
print("разберёмся, какого пакета не хватает.")
