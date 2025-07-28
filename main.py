import requests
import subprocess
import sys
import re
import os
import argparse
from bs4 import BeautifulSoup

def main():
    parser = argparse.ArgumentParser(description='Поиск пакетов')
    parser.add_argument('package_name', help='Пакет, который мы хотим проверить')
    args = parser.parse_args()

    # Первичный поиск
    params = {
        "terms": args.package_name,
        "match": "glob",
        "type": "package"
    }

    response = requests.get(f"https://koji.fedoraproject.org/koji/search", params=params)
    soup = BeautifulSoup(response.text, 'html.parser')
    total_packages = soup.find('strong', string=lambda t: t and 'through 50 of' in t)

    if not total_packages:
        print(f'Пакета с именем "{args.package_name}" не существует')
        exit()

    total_packages = total_packages.text.split('of')[-1].strip()
    total_packages = int(total_packages)
    total_pages = total_packages // 50 + (total_packages % 50 != 0)
    current_page = 1

    # Получение ID пакета
    q = soup.find('a', href=lambda href: href and 'packageID=' in href)
    if not q:
        print("Не удалось найти ID пакета")
        exit()
        
    href = q['href']
    package_id = href.split('packageID=')[1].split('&')[0]

    package_names = []
    build_ids = []
    pattern = r"\.fc"

    # Получение всех пакетов
    while current_page <= total_pages:
        soup = BeautifulSoup(response.text, 'html.parser')
        for row in soup.select('table.nested.data-list tr.row-odd, table.nested.data-list tr.row-even'):
            link = row.find('a', href=lambda href: href and 'buildinfo?buildID=' in href)
            if link and re.search(pattern, link.text):
                package_names.append(link.text)
                build_id = link['href'].split('buildinfo?buildID=')[1]
                build_ids.append(build_id)

        if current_page < total_pages:
            new_params = {
                "buildStart": 50 * current_page,
                "packageID": int(package_id),
                "buildOrder": "-completion_time",
                "tagOrder": "name",
                "tagStart": "0"
            }
            response = requests.get(f"https://koji.fedoraproject.org/koji/packageinfo", params=new_params)
        current_page += 1

    if not package_names:
        print("Не найдено подходящих пакетов")
        exit()

    # Пользовательский интерфейс выбора пакета
    current_page = 1
    total_pages = len(package_names) // 50 + (len(package_names) % 50 != 0)
    selected_pkg = None

    while True:
        print(f'\nПакеты с {(current_page - 1) * 50 + 1} по {min(len(package_names), current_page * 50)} из {len(package_names)}:')
        for i in range((current_page-1)*50, min(len(package_names), current_page*50)):
            print(f"{i+1}. {package_names[i]} (BuildID: {build_ids[i]})")

        print(f"\nСтраница {current_page} из {total_pages}")
        print("Введите номер пакета для установки или:")
        print("n - следующая страница, p - предыдущая страница, q - выход")
        
        user_input = input().strip().lower()
        
        if user_input == 'n':
            current_page = min(current_page + 1, total_pages)
        elif user_input == 'p':
            current_page = max(current_page - 1, 1)
        elif user_input == 'q':
            exit()
        else:
            try:
                selected_idx = int(user_input) - 1
                if 0 <= selected_idx < len(package_names):
                    selected_pkg = package_names[selected_idx]
                    selected_build_id = build_ids[selected_idx]
                    break
                else:
                    print("Неверный номер пакета")
            except ValueError:
                print("Неверный ввод")

    # Проверка установленной версии
    installed_version = None
    try:
        result = subprocess.run(['rpm', '-q', args.package_name], capture_output=True, text=True)
        if result.returncode == 0:
            installed_version = result.stdout.strip()
    except subprocess.SubprocessError as e:
        print(f"Ошибка при проверке пакета: {e}")
        exit()

    if installed_version:
        print(f"\nУстановленная версия: {installed_version}")
        print(f"Выбранная версия: {selected_pkg}")
        
        # Сравнение версий
        installed_ver_num = installed_version.split(args.package_name + '-')[1].split('.fc')[0]
        selected_ver_num = selected_pkg.split(args.package_name + '-')[1].split('.fc')[0]
        
        if installed_ver_num >= selected_ver_num:
            print("Установленная версия новее или равна выбранной. Установка не требуется.")
            exit()
            
        print("Удалить текущую версию перед установкой? (Y/N)")
        user_reply = input().strip().upper()
        if user_reply == 'Y':
            try:
                subprocess.run(['sudo', 'dnf', 'remove', '-y', args.package_name], check=True)
                print('Пакет успешно удален.')
            except subprocess.SubprocessError as e:
                print(f"Ошибка при удалении пакета: {e}")
                exit()

    # Скачивание и установка
    print(f'\nСкачивание пакета {selected_pkg}...')
    build_info_url = f"https://koji.fedoraproject.org/koji/buildinfo?buildID={selected_build_id}"
    response = requests.get(build_info_url)
    
    if "complete" not in response.text:
        print("Сборка не завершена, невозможно скачать")
        exit()

    soup = BeautifulSoup(response.text, 'html.parser')
    rpm_link = None
    
    # Ищем ссылку на RPM (предпочитаем x86_64, потом noarch)
    for arch in ['x86_64', 'noarch', 'i686']:
        for a in soup.find_all('a', href=True):
            if f"{selected_pkg}.{arch}.rpm" in a['href']:
                rpm_link = a['href']
                break
        if rpm_link:
            break

    if not rpm_link:
        print("Не удалось найти ссылку для скачивания RPM")
        exit()

    print(f"Найдена ссылка: {rpm_link}")
    print("Скачивание...")
    
    try:
        rpm_response = requests.get(rpm_link, stream=True)
        rpm_response.raise_for_status()
        
        rpm_filename = rpm_link.split('/')[-1]
        with open(rpm_filename, 'wb') as f:
            for chunk in rpm_response.iter_content(chunk_size=8192):
                f.write(chunk)
                
        print(f"Файл {rpm_filename} успешно скачан")
        
        print("Установить пакет? (Y/N)")
        user_reply = input().strip().upper()
        if user_reply == 'Y':
            try:
                subprocess.run(['sudo', 'dnf', 'install', '-y', rpm_filename], check=True)
                print("Пакет успешно установлен")
                
                # Удаляем скачанный RPM после установки
                os.remove(rpm_filename)
                print(f"Файл {rpm_filename} удален")
            except subprocess.SubprocessError as e:
                print(f"Ошибка при установке: {e}")
        else:
            print("Установка отменена")
            # Можно оставить файл или удалить его
            print(f"Скачанный файл: {rpm_filename}")
            
    except Exception as e:
        print(f"Ошибка при скачивании: {e}")

if __name__ == '__main__':
    main()
