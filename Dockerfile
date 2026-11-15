FROM mambaorg/micromamba:1.5.10

COPY --chown=$MAMBA_USER:$MAMBA_USER environment.yml /tmp/environment.yml
RUN micromamba install -y -n base -f /tmp/environment.yml && micromamba clean --all --yes

WORKDIR /app
COPY --chown=$MAMBA_USER:$MAMBA_USER . /app

ENV APP_ENV=prod
# Render가 정해 주는 포트(PORT)로 켠다. 로컬에서는 8080
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]