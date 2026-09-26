@echo off
REM Runs the Research Agent with secrets injected live by Doppler --
REM nothing touches disk as a plaintext .env. One-time setup: create a
REM Service Token in the Doppler dashboard for the tpl-brain project/config,
REM then run once (in a normal terminal, not this file):
REM     setx DOPPLER_TOKEN "dp.st.xxxxxxxx"
REM Close and reopen your terminal after that so the variable takes effect.

if "%DOPPLER_TOKEN%"=="" (
    echo ERRO: variavel de ambiente DOPPLER_TOKEN nao definida.
    echo Rode uma vez em um terminal normal: setx DOPPLER_TOKEN "dp.st...."
    echo Feche e abra um novo terminal depois disso, e rode este arquivo de novo.
    exit /b 1
)

doppler run --token %DOPPLER_TOKEN% --project tpl-brain --config dev -- python run.py %*
