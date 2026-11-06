# Palavra do Dia

Aplicativo cristão de leitura e reflexão bíblica. O projeto inclui o pacote
oficial USFM da Bíblia Livre (BLIVRE), redistribuído sob CC BY 4.0 com
atribuição. A migração e a importação para cada banco são explícitas.

## Requisitos

- Python 3.11 ou superior
- `pip`

## Preparar o ambiente no Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Executar

```powershell
python -m flask --app app:create_app run
```

Abra `http://127.0.0.1:5000/` no navegador. Na primeira inicialização, o
esquema versionado é aplicado ao banco em
`instance\palavra-do-dia.sqlite3`. O catálogo contém os 66 livros canônicos;
capítulos e versículos BLIVRE só são gravados no banco ao executar o comando
explícito de importação. O caminho pode ser alterado pela variável
`PALAVRA_DO_DIA_DATABASE`; caminhos relativos são resolvidos a partir da pasta
`instance`.

O fuso da leitura diária usa `PALAVRA_DO_DIA_TIMEZONE`, com padrão
`America/Sao_Paulo`. Use um identificador IANA válido. O arquivo
`.env.example` documenta as variáveis, mas não é carregado automaticamente;
configure-as no ambiente do terminal antes de iniciar o Flask.

Para instalar também as ferramentas de desenvolvimento e testes:

```powershell
python -m pip install -r requirements-dev.txt
```

## Publicação gratuita no PythonAnywhere

Esta seção prepara a publicação, mas não executa deploy nem envia arquivos.

### 1. Criar o ambiente

No PythonAnywhere, crie um virtualenv com a mesma versão de Python do Web App
e instale somente as dependências de produção:

```bash
mkvirtualenv --python=/usr/bin/python3.13 palavra-do-dia
workon palavra-do-dia
pip install -r /home/SEU_USUARIO/palavra-do-dia/requirements.txt
```

As dependências de teste estão separadas em `requirements-dev.txt` e não são
necessárias para o Web App.

### 2. Configurar variáveis de ambiente

Defina, no ambiente do Web App ou antes de iniciar o processo:

```text
PALAVRA_DO_DIA_ENV=production
PALAVRA_DO_DIA_SECRET_KEY=<chave-aleatoria-persistente>
PALAVRA_DO_DIA_DATABASE=/home/SEU_USUARIO/palavra-do-dia-data/palavra-do-dia.sqlite3
PALAVRA_DO_DIA_TIMEZONE=America/Sao_Paulo
```

`PALAVRA_DO_DIA_SECRET_KEY` não deve ser gravada no código, no `.env` enviado
ou no repositório. Em produção, a aplicação recusa iniciar sem ela. O
SQLite deve ficar em um diretório persistente e gravável da conta, fora de
diretórios temporários. O banco original local não deve ser enviado ao
GitHub; crie ou restaure o banco somente no armazenamento persistente do
servidor, conforme o procedimento autorizado de importação.

### 3. Configurar o WSGI

No painel **Web** do PythonAnywhere, escolha configuração manual, informe o
virtualenv e edite o arquivo WSGI para apontar para o projeto. O arquivo
[`wsgi.py`](./wsgi.py) já expõe `application` usando `create_app()`. Se o
projeto estiver em `/home/SEU_USUARIO/palavra-do-dia`, use:

```python
import sys

project_root = "/home/SEU_USUARIO/palavra-do-dia"
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from wsgi import application
```

Não use `flask run` nem o servidor de desenvolvimento na publicação. Após
configurar as variáveis e o WSGI, use **Reload** no painel e confira os logs
do Web App. As migrações versionadas são aplicadas quando a aplicação inicia;
faça backup do SQLite persistente antes de atualizações.

### 4. Checklist antes do primeiro deploy

- confirmar que `PALAVRA_DO_DIA_SECRET_KEY` está definida;
- confirmar que o caminho configurado do SQLite é persistente e gravável;
- executar as migrações/importações autorizadas diretamente no servidor;
- verificar a atribuição e os créditos CC BY 4.0 da BLIVRE;
- não enviar `instance/`, bancos, backups, logs ou `.env`;
- testar isolamento entre navegadores e operações CSRF;
- configurar backups do SQLite conforme a política da hospedagem.

## Preparar um repositório GitHub com segurança

O projeto não inclui um repositório Git local por padrão. Antes de inicializar
um, confira os arquivos ignorados e não adicione bancos, backups, arquivos
`.env`, logs, caches, artefatos temporários ou chaves/assinaturas locais.

No PowerShell, a sequência segura é:

```powershell
git init
git add .
git status --short
git diff --cached --name-only
```

Revise a lista do `git status` e remova do estágio qualquer arquivo privado
antes de criar o primeiro commit. O banco em `instance/`, arquivos `*.sqlite3`,
`*.db`, `*.bak`, `*.env` e artefatos `*.asc` estão protegidos pelo
`.gitignore`. Não execute `git push` até revisar o conteúdo staged, o histórico
e as configurações de acesso do repositório.

A interface inicial apresenta a data nesse fuso e consulta a leitura
editorialmente atribuída à data e à tradução. O seletor lista traduções
aprovadas com conteúdo importado. Sem uma leitura editorialmente atribuída
para a data selecionada, a página mostra um estado vazio e não escolhe um texto
aleatório. Não há painel administrativo nem atribuições automáticas.

A página inicial consulta apenas leituras editoriais registradas para a data
local e a tradução selecionada. Se houver várias traduções aprovadas com
leitura para aquele dia, a pessoa escolhe uma; se não houver conteúdo
autorizado, a tela informa a ausência sem criar uma passagem substituta.
Compartilhamento usa a API do navegador ou copia a leitura quando suportado.
A navegação de Bíblia permite selecionar a tradução, livro e capítulo,
visualizar versículos e notas e favoritar um versículo importado. Créditos e
licença são exibidos junto ao conteúdo. As áreas Reflexões, Favoritos e Meu
Progresso também têm rotas reais.

A tabela `daily_readings` guarda a data, tradução e intervalo de versículos
atribuído editorialmente. O pacote editorial inicial cadastra leituras para
5 a 11 de novembro de 2026 somente em BLIVRE; não cria calendário automático
para outras datas. Para uma referência ser exibida, os versículos inicial,
final e todos os números intermediários devem existir na tradução aprovada.
Caso contrário, a publicação é bloqueada ou o conteúdo permanece oculto.

## Reflexões e orações

`/reflexoes` mostra somente reflexões publicadas, em ordem cronológica, com
paginação; `/reflexoes/<id>` abre o detalhe. A home integra à leitura do dia
uma reflexão publicada que esteja associada ao mesmo registro
`daily_readings`. Rascunhos, registros sem aprovação de tradução ou com
intervalo de versículos incompleto não são exibidos. Sem conteúdo, a interface
mostra uma mensagem de estado vazio.

A migração `003_reflections.sql` cria a tabela editorial com título, reflexão,
explicação simples, aplicação prática, oração, pergunta, autoria, estado e data
de publicação. O arquivo `data/editorial/blivre-initial-week.json` contém sete
reflexões originais para 5–11 de novembro de 2026, associadas a passagens
verificadas na BLIVRE. Ele guarda apenas referências e conteúdo editorial;
nenhum texto bíblico é duplicado nele. O comando de publicação confirma a
tradução/licença, valida todos os intervalos, exige confirmação explícita e
insere leituras/reflexões em uma transação. Uma reexecução idêntica não duplica
nem sobrescreve conteúdo; conflitos bloqueiam a publicação.

Não há painel editorial. Novos conteúdos devem ser originais ou licenciados,
revisados e associados a leituras válidas. A oração pode ser copiada pelo botão
quando a API de clipboard ou o fallback do navegador estiver disponível; o
resultado é informado na interface.

## Favoritos

`/favoritos` lista os versículos e as reflexões salvos, com filtros por tipo.
Os registros são isolados por um identificador anônimo aleatório mantido na
sessão persistente e protegida de cada navegador. Não há contas nem
sincronização em nuvem; limpar os cookies ou trocar de navegador inicia outro
espaço pessoal. Registros antigos com o proprietário legado
`local-installation` não são expostos a novos visitantes.

A migração aditiva `004_favorites.sql` mantém a relação com o versículo ou a
reflexão de origem, evita duplicações por visitante e impede apagar registros
de conteúdo ainda referenciados. Cada versículo é salvo individualmente com
referência e cópia do texto, e só pode ser adicionado a partir de uma tradução
aprovada e de um versículo completo. Reflexões só podem ser salvas quando estão
publicadas e associadas a uma leitura completa/autorizada. Não há leituras
diárias além da semana editorial inicial nem conteúdo fora das reflexões
cadastradas.

As ações de salvar e remover usam formulários POST com token anti-CSRF. Os
textos bíblicos continuam sujeitos às permissões de armazenamento e exibição
da tradução aprovada; o favorito guarda apenas uma cópia local do texto já
autorizado no banco da aplicação.

## Meu Progresso

`/progresso` mostra o calendário mensal, os dias de leitura concluídos, o total,
a sequência atual e a maior sequência, além do histórico e da meta semanal
definida pela pessoa. O progresso é isolado por navegador, sem contas ou sincronização em nuvem. As
datas e sequências usam `America/Sao_Paulo`, configurado por
`PALAVRA_DO_DIA_TIMEZONE`.

A migração aditiva `005_reading_progress.sql` cria registros de conclusão e
metas semanais com chaves estrangeiras, restrições de duplicidade e índices.
Uma conclusão só pode ser criada para uma leitura completa, cadastrada para a
data local atual e ligada a uma tradução aprovada. A ação de desfazer remove
somente a conclusão local correspondente. Metas de 1 a 7 dias por semana são
persistidas no SQLite. Nenhum registro de leitura ou meta é pré-carregado.
Com o banco sem conteúdo autorizado, o painel apresenta calendário e histórico
vazios sem inventar atividade.

## Recursos pessoais

A migração aditiva `007_personal_features.sql` acrescenta anotações associadas
a versículos autorizados, marcações por cor e categoria integradas aos
favoritos, diário local de oração e preferências persistentes de tema, fonte e
espaçamento. As operações de alteração usam proteção CSRF e validação no
servidor.

Esses recursos, assim como favoritos e progresso, pertencem ao navegador
identificado pela sessão anônima. O projeto não possui autenticação, contas
individuais ou sincronização em nuvem.

### Identidade anônima e produção

Cada navegador recebe um identificador aleatório seguro, assinado pelo Flask e
armazenado em cookie persistente `HttpOnly`, `SameSite=Lax` e, em produção,
`Secure`. Todas as consultas e alterações pessoais recebem o identificador
validado no servidor; IDs de outro visitante retornam `404`.

Defina `PALAVRA_DO_DIA_ENV=production` e uma chave persistente em
`PALAVRA_DO_DIA_SECRET_KEY` antes de iniciar uma publicação. A aplicação recusa
iniciar em produção sem essa variável. Em desenvolvimento, uma chave
temporária é gerada para a execução atual.

## Experiência de leitura e busca bíblica

A página Bíblia permite navegar rapidamente por livro e capítulo, incluindo a
transição entre o último capítulo de um livro e o primeiro do próximo. A rota
`/biblia/busca` pesquisa referências como `João 3:16` e palavras ou trechos
existentes nos versículos da tradução autorizada selecionada. Cada resultado
abre diretamente o capítulo e o versículo correspondente.

Durante a leitura, os seletores de livro e capítulo e o atalho de busca ficam
disponíveis junto aos controles de fonte e modo foco. Anotações, marcações e
favoritos continuam sendo operações locais protegidas por CSRF; a busca nunca
gera ou consulta textos bíblicos externos.

## Cartões de versículos

Em `/biblia/cartao` é possível informar uma referência válida ou usar o atalho
**Criar cartão** disponível em cada versículo. A rota consulta exclusivamente
uma tradução aprovada no banco; referências inválidas não geram imagem. O
editor oferece três modelos, três cores, duas tipografias e dois alinhamentos.
A pré-visualização é desenhada em Canvas no navegador e pode ser baixada como
PNG ou compartilhada quando o navegador oferecer essa API. O texto, a
referência, a identificação `BLIVRE/porbr2018` e a atribuição CC BY 4.0 são
incorporados ao cartão. Nenhuma imagem ou texto bíblico é enviado a serviço
externo.

## Conquistas

`/conquistas` mostra conquistas desbloqueadas e pendentes para leitura,
sequências, capítulos, livros e metas semanais. O resumo também aparece em
`/progresso`. As conquistas são calculadas somente a partir de registros reais
de conclusão; um capítulo distinto é contado uma única vez e um livro só é
considerado concluído quando todos os seus capítulos têm leitura concluída.
Os desbloqueios e semanas alcançadas são protegidos por chaves únicas na
migração aditiva `008_achievements.sql`. Desfazer uma conclusão recalcula os
indicadores e remove apenas desbloqueios que deixaram de ser elegíveis. As
datas usam o fuso `America/Sao_Paulo`.

## Testes

```powershell
python -m pytest -q
python -m compileall -q app tests
```

Os testes usam bancos temporários. O único texto de versículo de demonstração
é sintético, explicitamente não bíblico, e fica nos fixtures de teste. A
validação final desta entrega aprovou 112 testes; o banco ativo permaneceu com
66 livros, 1.189 capítulos, 31.102 versículos e 1.290 notas BLIVRE, sem dados
pessoais de teste.

## Bíblia Livre (BLIVRE)

O arquivo `data/imports/blivre/porbr2018_usfm.zip` é o pacote oficial
`porbr2018` obtido do eBible.org, em USFM. O importador confere o SHA-256
verificado do pacote antes de processá-lo e importa os 66 livros na ordem
protestante, preservando capítulos, versículos e notas USFM. Ele valida 1.189
capítulos e 31.102 versículos e registra 1.290 notas em tabela separada. A
migração aditiva `006_verse_notes.sql` cria essa estrutura sem substituir
tabelas existentes.

Fonte: [eBible.org — porbr2018](https://ebible.org/find/details.php?id=porbr2018).
Licença: [Creative Commons Atribuição 4.0 Brasil (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/br/).

> Todas as Escrituras em português são da Bíblia Livre (BLIVRE), Copyright
> © 2018 Diego Santos, Mario Sérgio e Marco Teles. Fonte:
> [Bíblia Livre](https://sites.google.com/site/biblialivre/). Licença Creative
> Commons Atribuição 4.0 Brasil:
> [CC BY 4.0 Brasil](https://creativecommons.org/licenses/by/4.0/br/).

Importe no banco ativo somente após revisar a fonte e confirmar a atribuição:

```powershell
python -m flask --app app:create_app import-blivre `
  --confirm-license `
  --reviewed-by "Nome do responsável" `
  --reviewed-at "AAAA-MM-DD" `
  --acquired-at "AAAA-MM-DD"
```

O comando não baixa arquivos, bloqueia sem confirmação, é idempotente para o
mesmo pacote e recusa sobrescrever uma BLIVRE já registrada com origem/hash
diferente. A importação da tradução não cria `daily_readings`: cada leitura do
dia continua dependendo de atribuição editorial própria. O arquivo original
está preservado e não é alterado pelo parser.

## Palavra do Dia e conteúdo editorial inicial

As leituras diárias de 5 a 11 de novembro de 2026 e as reflexões associadas
estão em `data/editorial/blivre-initial-week.json`. Referências usadas:

- Salmos 23:1–3
- Provérbios 3:5–6
- Mateus 6:33
- João 15:4–5
- Efésios 2:8–10
- Romanos 12:1–2
- Filipenses 4:6–7

O texto bíblico é sempre lido do banco BLIVRE; o arquivo editorial não contém
cópias nem paráfrases apresentadas como Escritura. Para publicar a semana no
banco ativo:

```powershell
python -m flask --app app:create_app publish-initial-editorial --confirm-publish
```

O comando valida tradução/licença, referências completas e conflitos antes
de inserir. A operação é atômica e idempotente para os mesmos registros. A
home, o histórico, favoritos e compartilhamento usam os serviços e rotas já
existentes; a atribuição BLIVRE continua visível.

## Importar outras traduções

Leia [data/README.md](data/README.md) para o formato offline de importação e a
política de proveniência/licença. Não há download automático ou scraping. Para
outras traduções, o operador precisa obter o conteúdo de uma fonte autorizada,
preparar o JSON localmente e verificar que a licença permite armazenamento,
exibição e atribuição para o uso pretendido.

Após essa verificação, configure `PALAVRA_DO_DIA_DATABASE` se necessário e
execute no PowerShell:

```powershell
python -m flask --app app:create_app import-scripture "C:\caminho\traducao.json" `
  --confirm-license `
  --reviewed-by "Nome do responsável" `
  --reviewed-at "AAAA-MM-DD" `
  --evidence-url "https://fonte-oficial.example/licenca"
```

O comando exige confirmação explícita, registra quem revisou e quando, os
metadados de licença e atribuição, origem/data de aquisição, SHA-256 do arquivo
importado e contagens. Uma tradução existente não pode ser sobrescrita; a
importação é transacional e falhas não deixam dados parciais.

**Limite importante:** o importador valida estrutura, referências canônicas,
restrições relacionais e proveniência declarada. Ele não comprova a
autenticidade dos versículos, não verifica juridicamente uma licença nem
contata a URL de evidência. Não confirme a importação sem revisão humana da
fonte e dos direitos. Até uma fonte/tradução ser aprovada, nenhuma importação
outra importação real não está autorizada sem verificação e confirmação
específicas da tradução.
