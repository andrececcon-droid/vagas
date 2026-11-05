# Busca diária de vagas

O GitHub roda o programa `buscar_vagas.py` sozinho às 7h e às 12h (horário de Brasília). Ele abre cada fonte de `fontes.json`, testa se a página carrega, procura vagas compatíveis com o seu perfil, confere se os links abrem e atualiza a página `docs/index.html`. O histórico de cada rodada fica em `dados/historico.json`.

## Como ativar (uma vez só, uns 15 minutos)

1. Crie uma conta grátis em github.com.
2. Clique em **New repository**, dê o nome `vagas`, marque **Public** e crie.
3. Na página do repositório, clique em **uploading an existing file** e envie TODO o conteúdo desta pasta, mantendo as subpastas `.github/workflows`, `docs` e `dados`. Se o site não aceitar pastas ocultas, crie o arquivo `.github/workflows/vagas.yml` pelo botão **Add file > Create new file** e cole o texto.
4. Vá em **Settings > Pages**, escolha **Deploy from a branch**, branch `main`, pasta `/docs`, e salve.
5. Vá em **Actions**, abra **Buscar vagas** e clique em **Run workflow**. Leva alguns minutos.
6. Seu painel fica em `https://SEU-USUARIO.github.io/vagas/`. Salve esse endereço nos favoritos.

## Cuidados

- O repositório e a página são **públicos** (é o que permite usar de graça). Não coloque dados pessoais, senhas ou telefone nos arquivos.
- A tabela "Situação de cada fonte" mostra quais fontes funcionam. Depois de uma ou duas semanas, apague de `fontes.json` as que sempre falham.
- LinkedIn, Indeed, Catho, Infojobs e iNeed não são lidos pelo programa. Ative o alerta por e-mail em cada um (cargo, Porto Alegre, últimas 24 horas).
- A fonte "META Assessoria" está sem link. Cole o endereço dela no campo `url` de `fontes.json`.
- A busca da Gupy usa a API pública do portal. Se a tabela mostrar "Falhou" nessa linha, me avise com a mensagem.
- Para mudar o horário, edite a linha `cron` do arquivo `vagas.yml` (os horários são em UTC, 3 horas à frente de Brasília).
- Para mudar os cargos buscados, edite as listas `ALTA` e `MEDIA` no início de `buscar_vagas.py`.
