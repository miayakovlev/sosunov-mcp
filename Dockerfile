# Образ приложения для OpenShift / CI. При необходимости замените базу на корпоративный mirror.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/app-root/python

WORKDIR /opt/app-root

COPY requirements.txt /opt/app-root/
# Корпоративный pip: отредактируйте pip.conf в репозитории или смонтируйте при сборке.
COPY pip.conf /etc/pip.conf

RUN pip install --no-cache-dir -r requirements.txt

COPY python/ /opt/app-root/python/

RUN useradd --uid 1001 --gid 0 --home-dir /opt/app-root --no-create-home app \
  && chown -R 1001:0 /opt/app-root \
  && chmod -R g=u /opt/app-root

USER 1001

EXPOSE 9101

CMD ["python", "/opt/app-root/python/server.py"]
