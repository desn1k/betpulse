# BetPulse на VPS: руководство оператора

Документ для вас как оператора сервера. Он ведёт от выбора провайдера до работающего сайта
по шагам: в каждом шаге — блок команд, ожидаемый вывод и что делать, если вывод другой.
Команды, имена файлов и содержимое конфигов приведены как есть, их нельзя переводить.
`HANDOFF.md` упоминается только как источник предыстории: для выполнения шагов он не нужен.

Язык документа — русский по решению владельца (исключение из правила «документация на
английском», записано в `AGENTS.md`).

## 0. Как пользоваться документом

Есть два пути, и они идут строго по очереди:

- **Путь A — закрытый пробный запуск** (разделы 2–7). Порты 80/443 открыты только для ваших
  адресов, сертификат выдаёт внутренний CA Caddy (браузер покажет предупреждение). На сайте
  есть только ваша собственная учётная запись администратора, поэтому
  персональные данные третьих лиц не обрабатываются.
  Цель — убедиться, что сервер, сеть и процедуры работают.
- **Путь B — публичный запуск** (раздел 9). Только после всего пути A и только когда выполнены
  все условия раздела 9, включая юридические.

Раздел 8 — повседневная эксплуатация, раздел 10 — одностраничный чек-лист пути A на день
запуска.

Формат шага:

- блок команд — выполнять целиком, по порядку;
- **Ожидаемо** — что должно появиться;
- **Если иначе** — что делать; если там написано «стоп», дальше не идти.

Обозначения в командах: `<ДОМЕН>` — имя сайта (например `bp.example.ru`); `<СЕРВЕР>` — IP или
имя сервера; `<ТЕГ>` — тег релиза (например `v0.0.1-rc7`); `<ПРЕДЫДУЩИЙ_ТЕГ>` — релиз перед
ним. Для пути A нужен релиз **не старше v0.0.1-rc7**: в нём есть переключатель сертификата
пробного запуска и команда `vapid-keys`.

Команды с `sudo` выполняются от пользователя `deploy` (раздел 3), остальные — от него же в
каталоге `~/betpulse`, если не сказано иное.

## 1. Размер сервера

### 1.1 Процессор и память

Ограничения из `infra/docker-compose.prod.yml` (резерваций нет ни у одного сервиса; лимит — это
потолок, а не выделенный объём):

| Сервис | CPU (лимит) | Память (лимит) | Измерено на стенде в покое |
|---|---|---|---|
| postgres | 2.0 | 4 GB | 56 MB |
| redis | 0.5 | 512 MB | 5 MB |
| api | 1.0 | 1 GB | 173 MB |
| worker-realtime | 1.0 | 1 GB | 203 MB |
| worker-batch | 1.0 | 1 GB | 204 MB |
| worker-ml | 2.0 | 2 GB | 203 MB |
| web | 0.5 | 512 MB | 70 MB |
| caddy | 0.5 | 256 MB | 13 MB |
| mlflow | без лимита | без лимита | **1.76 GB** |
| **Итого** | **8.5** | **10.25 GB + mlflow** | **≈ 2.7 GB**, CPU < 5 % |

Замер — `docker stats` на локальном стенде (rc6, 10 матчей в базе, 2026-10-10). Обучение на
полных данных его не повторяет: `worker-ml` может занять до своего лимита 2 GB, Postgres —
больше кэша при больших таблицах.

| | vCPU | RAM | swap | Диск (NVMe/SSD) |
|---|---|---|---|---|
| **Минимум** | 4 | 8 GB | 2 GB | 60 GB |
| **Комфортно** | 8 | 16 GB | 2 GB | 120 GB |

Минимум: 2.7 GB в покое + до 2 GB на обучение + 1–2 GB рабочих данных Postgres + ≈ 1 GB на
систему и Docker; одновременный пик всех сервисов в 8 GB не поместится. Комфортный размер
покрывает сумму всех лимитов (10.25 GB) плюс mlflow и систему. Сервер с почасовой оплатой
можно начать с минимума и увеличить (раздел 2).

### 1.2 Диск

Оценка по ширине строк, измеренной на загруженном сезоне (`pg_column_size`), с запасом
≈ 2,5× на индексы и служебные данные страниц. Это порядок величины, а не обещание.

| Что | Объём |
|---|---|
| Образы Docker: базовые (timescaledb, redis, caddy) | ≈ 2.9 GB |
| Образы одного релиза (api 1.46 + web 0.34 + mlflow 1.29 GB) | ≈ 3.1 GB на релиз; держите 2–3 для отката |
| Матчи: 7 сезонов × 6 лиг ≈ 13 944 матча, со статистикой и привязками | ≈ 20 MB |
| Коэффициенты, одна строка = один исход одного букмекера, ≈ 200 B | см. ниже |
| — только закрытие, 1X2, 10 букмекеров, 7 сезонов (0.42 млн строк) | ≈ 85 MB |
| — закрытие, 3 рынка (7 исходов), 20 букмекеров, 7 сезонов (1.95 млн) | ≈ 390 MB |
| — будущий сбор: 24 снимка на матч, 20 букмекеров, 1X2 | ≈ 575 MB **на сезон** |
| Прогнозы: каждое обучение пишет ≈ 13 944 × 6 методов × 3 исхода | ≈ 80 MB **на одно обучение** |
| MLflow: метаданные | десятки MB |
| MLflow: артефакты (модели) | оценка 1–5 MB на обучение (на стенде 25 KB на малых данных) |
| Логи контейнеров с ротацией из §3.4 | ≤ 270 MB |
| Локальные бэкапы (раздел 8.4) | ≈ размер базы за каждый запуск |

Главный источник роста — прогнозы: старые версии сейчас не удаляются, так что еженедельное
обучение даёт ≈ 4 GB в год. Следите за диском (§8.6): при заполнении 70 % освободите место или увеличьте диск.

## 2. Что проверить у провайдера до оплаты

Провайдер ещё не выбран; ниже — обязательные требования и проверка кандидата.

### 2.1 Обязательные требования

| Требование | Почему | Как проверить |
|---|---|---|
| Сервер **и бэкапы** в России | 152-ФЗ, ст. 18 ч. 5: базы с персональными данными граждан РФ должны быть в РФ (для пути B); бэкапы — тоже | договор и документация провайдера, страна дата-центра и хранилища снимков/объектов |
| Почасовая оплата и смена размера | начать с минимума (§1.1), увеличить без переезда | тарифы, «изменить конфигурацию» в панели |
| IPv6 с описанным способом выдачи | проверка F5 (§7.3) требует реального клиента по IPv6 | документация: статический адрес/подсеть (`/64`) или выдача через RA/SLAAC |
| Межсетевой экран провайдера | ufw не фильтрует порты, опубликованные Docker; закрыть 80/443 для всех, кроме вас, можно только снаружи сервера | правила по IPv4 **и** IPv6, по портам и адресам источника |
| Снимки диска | откат сервера целиком до установки/обновления | «снимок»/«snapshot» в панели, где хранятся, сколько стоят |

Желательно: Ubuntu 24.04 LTS в образах, консоль (VNC/serial) на случай потери SSH, NVMe.

### 2.2 Доступность внешних сервисов с адреса сервера

Возьмите самый дешёвый сервер у кандидата на час (почасовая оплата) и выполните:

```bash
sudo apt-get update && sudo apt-get install -y git curl
git clone https://github.com/desn1k/betpulse.git ~/betpulse
cd ~/betpulse
LLM_HOST=api.openai.com scripts/check-egress.sh
```

`LLM_HOST` — хост вашего LLM-провайдера (тот, что будет в Admin → LLM; без настройки —
`api.openai.com`).

**Ожидаемо:** таблица по каждому хосту — `ok (код)` в колонке IPv4, `ok (код)` или `no AAAA` в
колонке IPv6, затем строка `IPv4: every host reachable.` Коды 200/301/302/400/404/405/406/421 —
нормальны: проверяется только, что сервис отвечает.

**Если иначе:**
- `FAIL` в колонке IPv4 и итог `IPv4 unreachable: ...` — сервис недоступен с этого адреса:
  стоп, этот провайдер (или его площадка) не подходит, либо спросите поддержку.
- `403?` и строка `WARN: ... answered 403` — сервис, возможно, блокирует регион сервера.
  Для `api.openai.com` это типично для адресов в РФ: выберите LLM-провайдера, доступного из РФ,
  и проверьте его хост через `LLM_HOST`.
- `WARN: this server has no IPv6 default route` — IPv6 на сервере не настроен: сначала §3.3,
  потом повторите.
- `WARN: IPv6 unreachable: ...` — по IPv6 сервис недоступен; приложение сможет ходить по IPv4,
  но сообщите об этом провайдеру.

Удалите пробный сервер, если провайдер не подошёл.

## 3. Подготовка сервера

### 3.1 Система и пользователь deploy

Ubuntu 24.04 LTS. Первый вход — под пользователем, которого создал провайдер (часто `root`;
если это другой пользователь с `sudo`, начинайте каждую команду ниже с `sudo `):

```bash
adduser --disabled-password --gecos "" deploy
usermod -aG sudo deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
cp ~/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 600 /home/deploy/.ssh/authorized_keys
passwd deploy
timedatectl set-timezone Europe/Moscow
apt-get update && apt-get -y upgrade && apt-get install -y unattended-upgrades git curl
```

`passwd deploy` — пароль только для `sudo`; входить по паролю будет нельзя (§3.2).

**Ожидаемо:** в **новом** терминале `ssh deploy@<СЕРВЕР>` входит по ключу, `sudo -v` принимает
пароль.

**Если иначе:** не закрывайте текущую сессию; проверьте содержимое и права
`/home/deploy/.ssh/authorized_keys`.

### 3.2 SSH: только ключи, без root

Держите открытой вторую сессию, пока не проверите вход заново.

```bash
sudo tee /etc/ssh/sshd_config.d/10-betpulse.conf >/dev/null <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
sudo sshd -t && sudo systemctl reload ssh
```

**Ожидаемо:** `sshd -t` ничего не печатает; новый вход `ssh deploy@<СЕРВЕР>` работает,
`ssh root@<СЕРВЕР>` отклоняется.

**Если иначе:** удалите файл `/etc/ssh/sshd_config.d/10-betpulse.conf` из оставшейся сессии,
`sudo systemctl reload ssh`, разберитесь и повторите.

### 3.3 IPv6 на сервере

```bash
ip -6 addr show scope global
ip -6 route show default
```

**Ожидаемо:** глобальный адрес (не `fe80::`) и маршрут `default via ...`.

**Если маршрут приходит через RA** (в выводе `proto ra`): Docker включает форвардинг, после
чего Linux перестаёт принимать RA, и IPv6 пропадёт через несколько минут. Включите приём RA
при форвардинге (имя интерфейса — из вывода выше, например `eth0`):

```bash
echo 'net.ipv6.conf.eth0.accept_ra = 2' | sudo tee /etc/sysctl.d/60-betpulse-ipv6.conf
sudo sysctl --system | grep accept_ra
```

**Если адреса нет вовсе:** включите IPv6 в панели провайдера или настройте его по документации
провайдера; без IPv6 проверка §7.3 невозможна — стоп для пути B.

### 3.4 Docker

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "userland-proxy": false,
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
sudo systemctl restart docker
sudo usermod -aG docker deploy
```

Выйдите и войдите снова (группа `docker` применяется при входе), затем:

```bash
docker version --format '{{.Server.Version}}'
docker compose version
docker run --rm hello-world | grep Hello
```

**Ожидаемо:** версия сервера **27 или новее**, `Docker Compose version v2...`, строка
`Hello from Docker!`.

**Если иначе:** версия ниже 27 — удалите пакеты `docker.io`/`podman-docker`, если они стоят, и
повторите. `download.docker.com` недоступен — используйте зеркало провайдера (спросите
поддержку).

`userland-proxy: false` — рекомендация, не требование; ротация логов (`max-size`) обязательна,
иначе логи без предела растут на диске. Группа `docker` даёт права root — не добавляйте в неё
других пользователей.

### 3.5 Межсетевой экран провайдера

ufw **не** фильтрует порты, которые публикует Docker (80/443 Caddy). Закрывать их нужно в панели
провайдера. Правила на вход, для IPv4 и IPv6:

| Порт | Путь A | Путь B |
|---|---|---|
| 22/tcp | только ваши адреса | только ваши адреса |
| 80/tcp, 443/tcp | только ваши IPv4 и IPv6 (адрес клиента для проверки F5 — тоже) | все |
| остальное | закрыто | закрыто |

Проверка с вашей машины (не с сервера):

```bash
curl -4 -sS -o /dev/null --max-time 5 http://<СЕРВЕР_IPv4>/
curl -4 -sS --max-time 5 telnet://<СЕРВЕР_IPv4>:5432
```

**Ожидаемо (до первого деплоя):** обе команды — ошибка соединения или таймаут. Это ещё **не**
проверка межсетевого экрана: на портах пока ничего не слушает. Настоящая проверка — после
деплоя, в §7.2.

**Если иначе:** правила провайдера не применились — исправьте до деплоя.

## 4. Секреты

### 4.1 Генерация

На сервере, каждая команда — новое значение:

```bash
echo "SECRET_KEY=$(openssl rand -hex 32)"
echo "DATA_ENCRYPTION_KEY=$(openssl rand -hex 32)"
echo "REDIS_PASSWORD=$(openssl rand -hex 32)"
echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"
echo "ADMIN_PASSWORD=$(openssl rand -hex 16)"
```

Ключи Web Push (VAPID) создаются после первого деплоя (§5.6). Только буквы и цифры у
`REDIS_PASSWORD` и `POSTGRES_PASSWORD` обязательны: они попадают внутрь URL подключения.

### 4.2 Файл .env

```bash
cd ~/betpulse
cp .env.example .env
chmod 600 .env
nano .env
```

Задайте (остальное оставьте как в примере):

```dotenv
ENVIRONMENT=production
PUBLIC_DOMAIN=<ДОМЕН>
CADDY_TRIAL_TLS=local_certs
PUBLIC_BASE_URL=https://<ДОМЕН>
CORS_ALLOWED_ORIGINS=https://<ДОМЕН>
AUTH_COOKIE_SECURE=true
SECRET_KEY=<из 4.1>
DATA_ENCRYPTION_KEY=<из 4.1>
REDIS_PASSWORD=<из 4.1>
POSTGRES_PASSWORD=<из 4.1>
ADMIN_EMAIL=<ваш адрес>
ADMIN_PASSWORD=<из 4.1>
WEBPUSH_CONTACT_EMAIL=<ваш адрес>
TZ=Europe/Moscow
```

`CADDY_TRIAL_TLS=local_certs` — только для пути A (раздел 9 его убирает). Ключи провайдеров
данных и LLM вводятся позже в Admin → Providers / LLM, не в `.env`.

```bash
stat -c '%a %U' .env
```

**Ожидаемо:** `600 deploy`.

**Если иначе:** `chmod 600 .env && sudo chown deploy:deploy .env`.

### 4.3 Что хранить отдельно от бэкапов базы

Сохраните копию `.env` в менеджере паролей (не рядом с дампами). Особенно:

| Ключ | Что будет без него |
|---|---|
| `DATA_ENCRYPTION_KEY` | ключи провайдеров, LLM и секреты TOTP в дампе базы не расшифровать: дамп без ключа бесполезен для них |
| `SECRET_KEY` | все сессии и токены станут недействительны (все выйдут из аккаунтов) |
| `REDIS_PASSWORD` | приложение не подключится к Redis, пока пароль в `.env` и у Redis не совпадёт |
| `POSTGRES_PASSWORD` | приложение не подключится к базе из существующего тома |

Дамп базы и `.env` вместе — полный доступ к данным; храните их раздельно.

### 4.4 Токен GHCR только для чтения

Образы публичные, но вход снимает лимиты анонимной загрузки и понадобится, если пакеты станут
закрытыми. На GitHub: Settings → Developer settings → Personal access tokens (classic) → новый
токен **только** со scope `read:packages`, со сроком действия.

```bash
docker login ghcr.io -u <github-логин>
```

(вставьте токен как пароль)

**Ожидаемо:** `Login Succeeded`.

**Если иначе:** проверьте scope токена (`read:packages`) и логин.

## 5. Установка и первый деплой (путь A)

### 5.1 Код и файл digests релиза

Если пробный сервер из §2.2 стал основным, клон уже есть.

```bash
cd ~/betpulse
git fetch --tags && git checkout <ТЕГ>
curl -fLO https://github.com/desn1k/betpulse/releases/download/<ТЕГ>/release-<ТЕГ>.digests
cat release-<ТЕГ>.digests
```

**Ожидаемо:** `HEAD is now at ...`; четыре строки файла: `RELEASE_VERSION=<ТЕГ>` и три
`..._IMAGE_DIGEST=sha256:...`.

**Если иначе:** проверьте тег на странице релизов GitHub.

### 5.2 Проверка конфигурации

Конфигурация production требует тег и digests релиза, поэтому проверка получает их из файла:

```bash
cd ~/betpulse
scripts/check-egress.sh
env IMAGE_TAG=<ТЕГ> $(grep _IMAGE_DIGEST= release-<ТЕГ>.digests) scripts/check-compose-ports.sh
IMAGE_TAG=<ТЕГ> RELEASE_DIGESTS=release-<ТЕГ>.digests scripts/prod-compose.sh pull api worker-realtime worker-batch worker-ml web mlflow
```

**Ожидаемо:** `IPv4: every host reachable.`; затем строка
`caddy: CLOSED TRIAL - certificates from Caddy's own CA (CADDY_TRIAL_TLS=local_certs); ...` и
строка `OK: only caddy 80/tcp and 443/tcp are published; ...`.

**Если иначе:** `check-compose-ports.sh` печатает список проблем — исправьте `.env` по тексту
каждой строки (например `REDIS_PASSWORD is required`, `CADDY_TRIAL_TLS must be empty or
local_certs`) и повторите. `required variable IMAGE_TAG is missing` — команда запущена без
`env IMAGE_TAG=...` из блока выше. Последняя команда скачивает образы именно этого релиза (по
digests): три строки `Image ghcr.io/desn1k/betpulse-...:<ТЕГ>@sha256:... Pulled` (api, web, mlflow). Ошибка `denied` или `manifest unknown` — нет доступа к
образу или неверный тег: вход в GHCR (§4.4) или тег (§5.1); `Login Succeeded` сам по себе
доступ к образам не доказывает.

### 5.3 DNS

У регистратора: запись `A` для `<ДОМЕН>` → IPv4 сервера, `AAAA` → IPv6 сервера.

```bash
getent ahostsv4 <ДОМЕН> | head -1; getent ahostsv6 <ДОМЕН> | head -1
```

**Ожидаемо:** адреса сервера (обновление DNS может занять до часа).

### 5.4 Первый деплой

```bash
cd ~/betpulse
IMAGE_TAG=<ТЕГ> RELEASE_DIGESTS=release-<ТЕГ>.digests scripts/deploy.sh
echo "exit $?"
```

**Ожидаемо:** загрузка образов, миграции (`Running upgrade ...`), в конце
`Deployment of <ТЕГ> completed successfully.` и `exit 0`. Сеть Docker создаётся сама; перенос
Redis на AOF на пустом сервере не нужен.

**Если иначе:**
- `exit 1` — ничего не изменено; причина в тексте (например нет файла digests). Исправьте и
  повторите ту же команду.
- `exit 4` — первый деплой не прошёл, отката нет (откатываться не на что).
  `scripts/prod-compose.sh ps` и `scripts/prod-compose.sh logs --tail=100 api web` покажут
  причину; исправьте и повторите.

```bash
scripts/prod-compose.sh ps --format '{{.Service}} {{.Status}}'
```

**Ожидаемо:** 9 сервисов, у каждого `(healthy)`.

### 5.5 Сертификат пробного запуска в браузере

Откройте `https://<ДОМЕН>/` с вашей машины. Браузер предупредит о сертификате — это
ожидаемо: его выдал собственный CA Caddy. Чтобы убрать предупреждение, импортируйте корневой
сертификат:

```bash
cd ~/betpulse
docker cp "$(scripts/prod-compose.sh ps -q caddy)":/data/caddy/pki/authorities/local/root.crt ./caddy-root.crt
```

Скопируйте `caddy-root.crt` к себе (`scp deploy@<СЕРВЕР>:betpulse/caddy-root.crt .`) и добавьте в
доверенные корневые сертификаты браузера/системы. На время пробного запуска сайт присылает
HSTS: это безопасно, но **не** отправляйте домен в список HSTS preload, пока идёт пробный
запуск.

### 5.6 Ключи Web Push

```bash
cd ~/betpulse
scripts/prod-compose.sh run --rm --no-deps api python -m app.cli vapid-keys
```

**Ожидаемо:** две строки `WEBPUSH_VAPID_PRIVATE_KEY=...` и `WEBPUSH_VAPID_PUBLIC_KEY=...`.
Вставьте обе в `.env` (заменив пустые), затем повторите деплой того же релиза:

```bash
IMAGE_TAG=<ТЕГ> RELEASE_DIGESTS=release-<ТЕГ>.digests scripts/deploy.sh
```

**Ожидаемо:** `Deployment of <ТЕГ> completed successfully.`

**Если иначе:** `invalid choice: 'vapid-keys'` — релиз старше v0.0.1-rc7; возьмите новее.

## 6. Администратор

### 6.1 Создание

```bash
cd ~/betpulse
scripts/prod-compose.sh run --rm api python -m app.bootstrap create-admin
```

**Ожидаемо:** `Admin account ready: <ADMIN_EMAIL>` и
`You must change this password on first login before admin features unlock.`

**Если иначе:** `Refusing to create the admin: ADMIN_PASSWORD ...` — пароль в `.env` пустой или
слабый: §4.1, повторите. `An admin account already exists` — администратор уже создан.

После входа удалите `ADMIN_PASSWORD` из `.env` (оставьте пустым): пароль вы смените при
первом входе.

### 6.2 Первый вход: смена пароля и TOTP

1. `https://<ДОМЕН>/` → «Войти» → `ADMIN_EMAIL` и `ADMIN_PASSWORD`.
2. Сайт требует сменить пароль — задайте новый (сохраните в менеджере паролей).
3. Сайт ведёт к настройке двухфакторной защиты: отсканируйте QR-код приложением-аутентификатором
   (ключ под QR-кодом — тот же), введите код.
4. Откроется админка `/admin`. Выйдите и войдите снова: после пароля сайт спросит код.

**Если иначе:** код не принимается — проверьте время на телефоне (автоматическое) и на сервере
(`timedatectl`). Админка не открывается после смены пароля — шаг 3 не завершён.

### 6.3 Потерян аутентификатор

Нужен SSH-доступ к серверу:

```bash
cd ~/betpulse
scripts/prod-compose.sh run --rm api python -m app.cli reset-2fa --email <ADMIN_EMAIL>
```

Если телефон мог быть украден вместе с паролем, добавьте `--require-password-change`.

**Ожидаемо:** одна строка
`reset-2fa: <адрес>: two-factor authentication turned off (was on), N refresh tokens revoked,
password change required: no` (или `yes`). Секрет не печатается. Все сессии этого
администратора завершены, в журнале аудита — `auth.2fa.reset_by_operator`. Войдите с паролем:
сайт снова проведёт через настройку TOTP (§6.2, шаг 3).

**Если иначе:** `no user with that email; nothing changed` — проверьте адрес.

## 7. Проверка на сервере

Выполните всё до открытия сайта кому-либо, кроме вас.

### 7.1 Права .env и конфигурация

```bash
cd ~/betpulse
stat -c '%a %U' .env
env IMAGE_TAG=<ТЕГ> $(grep _IMAGE_DIGEST= release-<ТЕГ>.digests) scripts/check-compose-ports.sh
```

**Ожидаемо:** `600 deploy`; `caddy: CLOSED TRIAL ...` и `OK: only caddy 80/tcp and 443/tcp
are published; ...`.

### 7.2 Открытые порты

```bash
sudo ss -tlnp
```

**Ожидаемо:** на внешних адресах (`0.0.0.0`, `[::]`, адреса сервера) слушают только 22, 80 и 443.
`127.0.0.53` (DNS системы) и `127.0.0.1` допустимы.

**Если иначе:** стоп; любой другой порт на внешнем адресе — `scripts/check-compose-ports.sh`
должен был его поймать, сообщите разработчику.

Межсетевой экран провайдера, теперь, когда Caddy слушает 80/443. С **разрешённого** адреса
(ваша машина) и с **неразрешённого** (например телефон через мобильный интернет, если его
адрес не в списке §3.5), по IPv4 и IPv6:

```bash
curl -4 -sS -o /dev/null -w '%{http_code}\n' --max-time 10 --cacert caddy-root.crt https://<ДОМЕН>/healthz
curl -6 -sS -o /dev/null -w '%{http_code}\n' --max-time 10 --cacert caddy-root.crt https://<ДОМЕН>/healthz
```

**Ожидаемо:** с разрешённого адреса — `200`; с неразрешённого — таймаут (`Connection timed
out` или `Operation timed out`), без ответа сервера.

**Если иначе:** неразрешённый адрес получил ответ — правила провайдера не действуют на 80/443
(или на одно из семейств адресов): стоп, исправьте их в панели и повторите. Без телефона
используйте любой сервис проверки доступности портов из интернета.

### 7.3 Адреса клиентов по IPv4 и IPv6 (F5)

Нужны два клиента: один по IPv4, другой по IPv6 (например телефон на мобильном интернете;
test-ipv6.com покажет, что у вас), оба — с разрешённых в §3.5 адресов. Запустите на сервере:

```bash
cd ~/betpulse
scripts/diagnose-client-ip.sh 180
```

Пока он ждёт (180 с), откройте с **каждого** клиента, **не входя в аккаунт**, главную страницу
и страницу матча.

**Ожидаемо в конце:**

```text
IPv4 real client seen: yes
IPv6 real client seen: yes
Gateway / internal address seen: no
```

и новые гостевые ключи квот под каждым реальным адресом (IPv6 — как его `/64`). В админке
`/admin/system` компонент `client_ip` — `ok`.

**Если иначе:** стоп для пути B. `OUR NETWORK (collapse)` или
`Gateway / internal address seen: YES` — клиенты видны как адрес шлюза Docker; смотрите часть 1
вывода (IPv6 в сети Docker, правила NAT для 80/443, `docker-proxy`). `IPv6 real client seen: NO`
при работающем IPv6-клиенте — проверьте запись `AAAA` (§5.3) и IPv6 на сервере (§3.3).
Предыстория — HANDOFF, раздел 9m, F5.

### 7.4 Привязка повтора refresh к подсети (F13 B)

Только после §7.3. Проверяет, что «потерянный» ответ обновления сессии повторяется только
клиенту из той же подсети.

1. Клиент A (подсеть 1): войдите. В инструментах разработчика (Application → Cookies) скопируйте
   значения `bp_refresh` и `bp_csrf`.
2. В течение 60 секунд на клиенте A выполните дважды:

   ```bash
   curl -sS -X POST https://<ДОМЕН>/api/auth/refresh -H 'Cookie: bp_refresh=<значение>; bp_csrf=<csrf>' -H 'X-CSRF-Token: <csrf>' -o /dev/null -w '%{http_code}\n'
   ```

   (добавьте `--cacert caddy-root.crt` для сертификата пробного запуска).
3. Сразу после — тот же запрос с теми же cookie с клиента B из **другой** подсети (другая IPv4
   `/24` или другая IPv6 `/64`; например ноутбук через мобильный интернет, его адрес должен
   быть разрешён в §3.5). Не используйте сам сервер: его запросы к себе приходят как адрес
   шлюза Docker и испортят проверку §7.3.
4. Повторите шаги 1–3 для пары по IPv6.

**Ожидаемо:** на клиенте A первый запрос — `200` (ротация), второй — `200` (повтор), в журнале
аудита `/admin/audit` — `auth.token.refresh_replayed`. С клиента B — отказ (`401`), в журнале
`auth.token.refresh_conflict` или `auth.token.reuse_detected` с `meta.replay = "subnet_mismatch"`.

Отказ клиенту B завершает и сессию клиента A (так задумано: старый токен предъявлен из чужой
подсети) — войдите на A заново.

**Если иначе:** повтор с клиента B принят — стоп для пути B, сообщите разработчику.

### 7.5 Туннель к MLflow

На сервере:

```bash
docker run -d --rm --name mlflow-tunnel --network betpulse_default -p 127.0.0.1:5001:5001 alpine/socat:1.8.0.3 tcp-listen:5001,fork,reuseaddr tcp-connect:mlflow:5000
```

На вашей машине:

```bash
ssh -L 5001:127.0.0.1:5001 deploy@<СЕРВЕР>
```

Откройте `http://127.0.0.1:5001/` в браузере.

**Ожидаемо:** интерфейс MLflow. После работы — `docker stop mlflow-tunnel` на сервере.

**Если иначе:** `network betpulse_default not found` — стек не запущен (§5.4).

### 7.6 Web Push в Firefox

В Firefox откройте сайт, войдите, разрешите уведомления и подпишитесь на матч, который идёт
или скоро начнётся. Сервер должен достучаться до `updates.push.services.mozilla.com` (§2.2).

**Ожидаемо:** уведомление приходит, либо в логе есть успешная отправка:

```bash
scripts/prod-compose.sh logs --since 30m worker-realtime | grep -i push | tail -20
```

**Если иначе:** ошибки отправки в логе — проверьте `check-egress.sh` по IPv4 и IPv6.

### 7.7 Ручной откат по digests

Нужен второй релиз: `<ПРЕДЫДУЩИЙ_ТЕГ>` с его файлом digests (как в §5.1).

```bash
cd ~/betpulse
curl -fLO https://github.com/desn1k/betpulse/releases/download/<ПРЕДЫДУЩИЙ_ТЕГ>/release-<ПРЕДЫДУЩИЙ_ТЕГ>.digests
IMAGE_TAG=<ПРЕДЫДУЩИЙ_ТЕГ> RELEASE_DIGESTS=release-<ПРЕДЫДУЩИЙ_ТЕГ>.digests scripts/rollback.sh
echo "exit $?"
docker ps --format '{{.Names}} {{.Image}}' | grep betpulse- | sort
```

**Ожидаемо:** `Application images rolled back to <ПРЕДЫДУЩИЙ_ТЕГ>. Database migrations are
intentionally not downgraded.`, `exit 0`, и у api, трёх воркеров, web и mlflow — digests
`<ПРЕДЫДУЩИЙ_ТЕГ>` из его файла. Затем верните текущий релиз:

```bash
IMAGE_TAG=<ТЕГ> RELEASE_DIGESTS=release-<ТЕГ>.digests scripts/deploy.sh
```

**Если иначе:** откат не откатывает схему базы; если между релизами были несовместимые
миграции, старый код может не запуститься — верните текущий релиз командой выше. На пробном
запуске данные тестовые, поэтому проверка безопасна.

### 7.8 Автоматический откат (провоцируется один раз)

Сначала §7.7 до отката (стек на `<ПРЕДЫДУЩИЙ_ТЕГ>`). Затем деплой `<ТЕГ>` с остановкой его
api, как только тот станет healthy. Терминал 2 (до запуска деплоя):

```bash
new=$(sed -n 's/^API_IMAGE_DIGEST=//p' ~/betpulse/release-<ТЕГ>.digests)
until docker inspect betpulse-api-1 --format '{{.Config.Image}} {{.State.Health.Status}}' 2>/dev/null | grep -q "$new healthy"; do sleep 0.5; done; docker stop betpulse-api-1
```

Терминал 1:

```bash
cd ~/betpulse
IMAGE_TAG=<ТЕГ> RELEASE_DIGESTS=release-<ТЕГ>.digests scripts/deploy.sh; echo "exit $?"
```

**Ожидаемо:** `Deployment of <ТЕГ> failed; rolled back to <ПРЕДЫДУЩИЙ_ТЕГ>, which is healthy and
ready.`, примечание о схеме, `exit 2`; сайт открывается. Затем обычный деплой `<ТЕГ>` (как в
§7.7), `exit 0`. Так же это отрабатывалось на rc5 (HANDOFF, раздел 9i, «rc5 rehearsal»).

**Если иначе:** `exit 3` — откат тоже не удался: `scripts/prod-compose.sh ps`, логи api/web,
затем вручную `IMAGE_TAG=<ПРЕДЫДУЩИЙ_ТЕГ> scripts/rollback.sh`.

### 7.9 Сертификат публичного домена

Проверяется в пути B после переключения (§9.4): `curl -I https://<ДОМЕН>/healthz` без
`--cacert` отвечает `HTTP/2 200`.

## 8. Эксплуатация

### 8.1 Деплой нового релиза

```bash
cd ~/betpulse
git fetch --tags && git checkout <ТЕГ>
curl -fLO https://github.com/desn1k/betpulse/releases/download/<ТЕГ>/release-<ТЕГ>.digests
env IMAGE_TAG=<ТЕГ> $(grep _IMAGE_DIGEST= release-<ТЕГ>.digests) scripts/check-compose-ports.sh
scripts/backup-now.sh
IMAGE_TAG=<ТЕГ> RELEASE_DIGESTS=release-<ТЕГ>.digests scripts/deploy.sh; echo "exit $?"
```

Коды выхода `deploy.sh`: `0` — готово; `1` — отказ до изменений (причина в тексте, ничего не
изменено); `2` — сбой, автоматически возвращён предыдущий релиз и он работает; `3` — сбой и
откат тоже не удался (сайт может лежать: §7.8, «Если иначе»); `4` — сбой без автоматического
отката (нет предыдущего релиза или его digests). При 2, 3 и 4 текст сообщает, менялась ли схема
базы — она не откатывается.

Ручной откат — §7.7.

### 8.2 Разовые процедуры, о которых сообщит deploy.sh

`deploy.sh` сам отказывается (exit 1, ничего не изменено) и печатает процедуру:

- **Сеть Docker изменилась** (`The Docker network ... does not match`): сайт недоступен между
  шагами, данные сохраняются.

  ```bash
  scripts/prod-compose.sh down
  IMAGE_TAG=<ТЕГ> RELEASE_DIGESTS=release-<ТЕГ>.digests scripts/deploy.sh
  ```

  Никогда не добавляйте `-v` к `down`: это удалит тома с базой.
- **Redis без AOF** (`Redis runs without AOF and holds N keys`; бывает только на стеке, где шёл
  релиз до 2026-10-10): сайт работает.

  ```bash
  scripts/redis-enable-aof.sh
  IMAGE_TAG=<ТЕГ> RELEASE_DIGESTS=release-<ТЕГ>.digests scripts/deploy.sh
  ```

  Скрипт начинает с `BGSAVE` и копии дампа вне контейнера (`.release/redis/dump-<UTC>.rdb` и
  `.sha256`), затем включает AOF. Без него Redis после деплоя запустился бы пустым.

### 8.3 Восстановление Redis из копии

Когда: данные Redis испорчены или потеряны (очереди заданий, квоты, счётчики лимитов). Ключи,
записанные после копии, пропадут.

```bash
cd ~/betpulse
ls .release/redis/ .release/backups/*/ 2>/dev/null | grep '\.rdb$'
scripts/redis-restore.sh .release/redis/dump-<UTC>.rdb
scripts/redis-restore.sh .release/redis/dump-<UTC>.rdb --replace-current-data
```

Первый вызов без флага ничего не меняет: показывает, сколько ключей сейчас, и что будет
заменено.

**Ожидаемо (второй вызов):** сначала копия текущих данных (`Copying them first ...`), затем
`Stopping redis and putting the copy into its volume.`, временный Redis переводит копию в AOF
(`AOF is on in the volume: N keys`), в конце `Restored: Redis holds N keys, AOF yes.`. Redis
недоступен несколько секунд. Служебные ключи heartbeat воркеров с коротким сроком жизни могут к
этому моменту истечь — это нормально, воркеры пишут их заново.

**Если иначе:** `does not match ... .sha256` — копия повреждена, возьмите другую. После сбоя на
середине — `scripts/prod-compose.sh up -d redis` и повторите команду.

Отрепетировано на стенде 2026-10-10: удалённый ключ вернулся, ключ, записанный после копии,
исчез, срок жизни ключей сохранился, пароль по-прежнему требуется, все 9 сервисов healthy.

### 8.4 Бэкап вручную (пока нет автоматического)

Автоматических бэкапов вне сервера (WAL-G) ещё нет. До них — вручную, минимум перед каждым
деплоем и раз в день:

```bash
cd ~/betpulse
scripts/backup-now.sh
```

**Ожидаемо:** строки `football.dump: N tables with data`, `mlflow.dump: N tables with data`,
`mlflow-artifacts.tgz: N entries`, копия Redis, затем `Done: .../.release/backups/<UTC>
(checksums in SHA256SUMS).` Предупреждение `pg_dump: warning: there are circular foreign-key
constraints on this table: continuous_agg` — служебные таблицы TimescaleDB, нормально.

**Если иначе:** `Backup INCOMPLETE: ... has no SHA256SUMS` — этот каталог не бэкап; причина в
строке выше (`pg_dump failed`, `empty`, `failed`), исправьте и повторите.

Копия вне сервера хранится **только зашифрованной**: в дампах есть персональные данные, а
отдельное хранение `.env` их не защищает. Один раз заведите пароль бэкапов
(`openssl rand -base64 24`) и сохраните его в менеджере паролей — отдельно от `.env` и не там,
где лежат копии. Без этого пароля копию не прочитать.

На сервере — зашифровать каталог (gpg спросит пароль дважды):

```bash
cd ~/betpulse/.release/backups
tar czf - <UTC> | gpg --pinentry-mode loopback --symmetric --cipher-algo AES256 -o <UTC>.tgz.gpg
ls -l <UTC>.tgz.gpg
```

С вашей машины — забрать только зашифрованный файл в хранилище в РФ (раздел 9) и проверить,
что он расшифровывается и цел:

```bash
scp deploy@<СЕРВЕР>:betpulse/.release/backups/<UTC>.tgz.gpg .
mkdir -p check && gpg --pinentry-mode loopback -d <UTC>.tgz.gpg | tar xzf - -C check
cd check/<UTC> && sha256sum -c SHA256SUMS
```

**Ожидаемо:** каждая строка `...: OK`. Затем удалите каталог `check` — расшифрованные данные не
храните. Расшифровка для восстановления — те же две команды (`gpg -d ... | tar xzf -`).

Проверено 2026-10-10 на тестовых данных: шифрование, расшифровка и `sha256sum -c` проходят.
Незашифрованные каталоги на сервере удаляйте, когда копия проверена (держите 2–3 последних). Восстановление Postgres из этих дампов пока не отрепетировано — оно появится вместе
с автоматическими бэкапами.

### 8.5 Логи

```bash
cd ~/betpulse
scripts/prod-compose.sh logs --tail=200 api
scripts/prod-compose.sh logs --since 1h worker-realtime worker-batch worker-ml
scripts/prod-compose.sh logs -f caddy
```

Логи в формате JSON; секреты в них заменены на `***`. Каждый контейнер хранит до 3 файлов по
10 MB (§3.4).

### 8.6 Страница здоровья системы

`/admin/system` — общий статус и компоненты:

| Компонент | ok | Иначе |
|---|---|---|
| `postgres` | база отвечает | `error` — база недоступна: `scripts/prod-compose.sh ps`, логи postgres |
| `redis` | Redis отвечает | `error` — Redis недоступен; пароль в `.env` и у Redis должен совпадать |
| `redis_memory` | занято меньше 80 % от 256 MB | `degraded` выше 80 %: при 100 % Redis отказывает в записи (квоты, лимиты, очереди) — сообщите разработчику, лимит увеличивают в `infra/docker-compose.yml` |
| `ops_alerts` | Telegram для оповещений настроен | `not_configured` — нет `TELEGRAM_BOT_TOKEN`/`TELEGRAM_ALERT_CHAT_ID` |
| `client_ip` | клиенты видны своими адресами | `degraded` — запросы приходят как адрес сети Docker (F5): §7.3 |

Диск — отдельно: `df -h /` (§1.2).

## 9. Путь B: публичный запуск

Открывать сайт кому-либо, кроме вас, можно только когда выполнены **все** условия ниже. Пункты
с пометкой «уточнить у юриста» — список вопросов, а не юридическая консультация; этот документ
не является юридической консультацией.

### 9.1 Технические условия

- Путь A пройден полностью (раздел 10), включая §7.3 и §7.4.
- Сервер и бэкапы — в РФ (§2.1).
- Бэкапы вне сервера автоматизированы (WAL-G в хранилище в РФ, проверка восстановления) —
  будущий PR «Phase 14b» (HANDOFF, раздел 9i). До него ручной режим §8.4 допустим только с вашей
  дисциплиной ежедневного копирования.
- Регистрация пользователей (O2) — будущий PR «O2: регистрация» (HANDOFF, раздел 9m, очередь):
  форма регистрации с **отдельным, не отмеченным заранее** согласием на обработку персональных
  данных.
- Реальные данные — будущий PR «Sportmonks: адаптер и backfill» (HANDOFF, раздел 9l): данные
  Sportmonks вместо football-data, `licensed_for_production` по письменному подтверждению
  лицензии.

### 9.2 Юридические условия (до первого стороннего пользователя)

Каждый пункт — уточнить у юриста:

1. **Статус оператора** — решить, кто оператор персональных данных (физлицо, ИП, юрлицо): от этого
   зависят реквизиты и уведомления.
2. **Уведомление Роскомнадзора об обработке персональных данных** (152-ФЗ, ст. 22) — подать
   как оператор до начала обработки; номер из реестра — в `config/legal.ts`.
3. **Отдельное уведомление о трансграничной передаче** (152-ФЗ, ст. 12) — данные уходят
   сервисам за рубежом: Telegram, FCM (Google), Apple и Mozilla push. Подаётся отдельно от
   уведомления по п. 2.
4. **База данных и бэкапы в России** (152-ФЗ, ст. 18 ч. 5) — §2.1.
5. **Реквизиты оператора и сроки хранения** — заполнить заглушки в `frontend/config/legal.ts`
   (сейчас `[PLACEHOLDERS]`), снять пометку DRAFT после проверки юристом текстов
   `/legal/*`; реализовать удаление данных по истечении сроков и удаление аккаунта.
6. **Согласие на обработку** — отдельный документ `/legal/consent` (уже сделан, не встроен в
   соглашение); проверить текст.
7. **Порядок уведомления об утечке** — сообщить в Роскомнадзор в течение 24 часов с момента
   выявления инцидента (и в течение 72 часов — о результатах расследования): кто, как и через
   какой канал это делает.

### 9.3 Сертификат и порты

После 9.1 и 9.2:

```bash
cd ~/betpulse
nano .env
```

Удалите значение: `CADDY_TRIAL_TLS=` (пусто). В панели провайдера откройте 80/443 для всех
(§3.5, колонка «Путь B»).

```bash
IMAGE_TAG=<ТЕГ> RELEASE_DIGESTS=release-<ТЕГ>.digests scripts/deploy.sh
env IMAGE_TAG=<ТЕГ> $(grep _IMAGE_DIGEST= release-<ТЕГ>.digests) scripts/check-compose-ports.sh | head -1
```

**Ожидаемо:** `caddy: public certificates (ACME).`

### 9.4 Проверка публичного сертификата

```bash
curl -I https://<ДОМЕН>/healthz
scripts/prod-compose.sh logs --since 10m caddy | grep -i -E 'certificate obtained|error' | tail -5
```

**Ожидаемо:** `HTTP/2 200` без `--cacert` и без предупреждений; в логе caddy —
`certificate obtained successfully`.

**Если иначе:** ACME не прошёл — проверьте `A`/`AAAA` (§5.3) и что 80/443 открыты для всех;
Caddy повторит сам. Сертификат пробного запуска после переключения больше не отдаётся
(проверено в контейнере 2026-10-10), так что предупреждение браузера означает, что публичный
ещё не получен. После успеха удалите импортированный корневой сертификат пробного запуска из
браузера.

## 10. Чек-лист дня: путь A

Отмечайте по порядку. Пункт не совпал — сначала «Если иначе» в его разделе.

- [ ] Провайдер отвечает требованиям (§2.1), `check-egress.sh` — `IPv4: every host reachable.` (§2.2)
- [ ] Пользователь `deploy`, вход по ключу (§3.1)
- [ ] SSH без паролей и root (§3.2)
- [ ] IPv6: адрес и маршрут, `accept_ra = 2` при RA (§3.3)
- [ ] Docker ≥ 27, `daemon.json` с ротацией логов (§3.4)
- [ ] Межсетевой экран провайдера: 22 и 80/443 только для вас, IPv4 и IPv6 (§3.5)
- [ ] Секреты сгенерированы (§4.1), `.env` заполнен, `600 deploy` (§4.2)
- [ ] Копия `.env` в менеджере паролей, отдельно от бэкапов (§4.3)
- [ ] `docker login ghcr.io` — `Login Succeeded` (§4.4)
- [ ] Код на `<ТЕГ>`, файл digests скачан (§5.1)
- [ ] `check-compose-ports.sh` — `CLOSED TRIAL` и `OK` (§5.2), DNS `A`/`AAAA` (§5.3)
- [ ] Первый деплой — `exit 0`, 9 сервисов healthy (§5.4)
- [ ] Корневой сертификат Caddy импортирован (§5.5)
- [ ] Ключи VAPID в `.env`, повторный деплой (§5.6)
- [ ] `create-admin` (§6.1), первый вход: смена пароля и TOTP (§6.2)
- [ ] `reset-2fa` знаком и записан (§6.3)
- [ ] `.env` и конфигурация ещё раз (§7.1), порты — только 22/80/443 (§7.2)
- [ ] F5: `IPv4 real client seen: yes`, `IPv6 real client seen: yes`, шлюза нет (§7.3)
- [ ] F13 B: повтор только в своей подсети (§7.4)
- [ ] Туннель MLflow (§7.5), Web Push в Firefox (§7.6)
- [ ] Ручной откат и возврат (§7.7), автоматический откат — `exit 2` (§7.8)
- [ ] `backup-now.sh` и копия каталога с сервера (§8.4)
- [ ] `/admin/system`: всё `ok`, кроме `ops_alerts` без Telegram (§8.6)

Не открывать сайт другим людям: это путь B (раздел 9).
