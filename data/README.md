# Dados e importações de traduções

O pacote oficial BLIVRE descrito abaixo é fornecido em `imports/blivre/` com
autorização CC BY 4.0 e atribuição. Essa pasta também pode conter
arquivos locais obtidos de outras fontes autorizadas; não adicione conteúdo
cuja origem, licença ou permissão de armazenamento/exibição não tenha sido
confirmada. Não versione outro texto bíblico sem autorização.

## Bíblia Livre (BLIVRE)

- Edição: `porbr2018`, Bíblia Livre (BLIVRE).
- Fonte e detalhes: <https://ebible.org/find/details.php?id=porbr2018>.
- Pacote: `<raiz-do-projeto>/data/imports/blivre/porbr2018_usfm.zip` (USFM oficial),
  originalmente disponível em
  <https://ebible.org/Scriptures/porbr2018_usfm.zip>.
- SHA-256 esperado pelo importador:
  `7ca6742fc1a32ca657a9872047c919097bb9af1384dc318c9cc58ab1cfa5bbf9`.
- Licença: Creative Commons Atribuição 4.0 Brasil (CC BY 4.0),
  <https://creativecommons.org/licenses/by/4.0/br/>.
- Escopo verificado: armazenamento e exibição com atribuição.

Atribuição exigida:

> Todas as Escrituras em português são da Bíblia Livre (BLIVRE), Copyright
> © 2018 Diego Santos, Mario Sérgio e Marco Teles. Fonte:
> <https://sites.google.com/site/biblialivre/>. Licença Creative Commons
> Atribuição 4.0 Brasil: <https://creativecommons.org/licenses/by/4.0/br/>.

O pacote importado foi validado como 66 livros, 1.189 capítulos, 31.102
versículos e 1.290 notas. Notas de rodapé e referências cruzadas são
armazenadas separadamente do texto do versículo. O parser não modifica o
arquivo-fonte; só aceita o hash verificado acima e bloqueia arquivo divergente.

Para importar no banco ativo, depois de revisar a fonte e confirmar a licença:

```powershell
python -m flask --app app:create_app import-blivre `
  --confirm-license `
  --reviewed-by "Nome do responsável" `
  --reviewed-at "AAAA-MM-DD" `
  --acquired-at "AAAA-MM-DD"
```

O comando não baixa o pacote nem cria leituras diárias. Ele é repetível sem
duplicar a importação do mesmo hash e recusa sobrescrever outro registro
`blivre`. A atribuição é mostrada na interface Bíblia, na leitura diária
quando cadastrada e nos favoritos.

## Formato JSON (versão 1)

O importador aceita um objeto JSON UTF-8 com estes campos:

| Caminho | Tipo | Descrição |
|---|---|---|
| `format_version` | inteiro | Deve ser `1`. |
| `translation.code` | texto | Identificador único em minúsculas, como definido pelo responsável. |
| `translation.name` | texto | Nome exato da edição/tradução. |
| `translation.language_code` | texto | Código do idioma. |
| `translation.rights_holder` | texto | Titular indicado pela fonte/licença. |
| `translation.license.name` | texto | Nome da licença ou autorização. |
| `translation.license.url` | URL HTTPS | Termos oficiais aplicáveis. |
| `translation.license.scope` | texto | Escopo verificado, inclusive armazenamento e exibição. |
| `translation.license.attribution` | texto | Atribuição exigida, reproduzida conforme os termos. |
| `source.name` | texto | Nome do publicador/fonte do arquivo. |
| `source.url` | URL HTTPS | Página oficial de origem do conteúdo. |
| `source.acquired_at` | data ISO | Data em que o arquivo foi obtido. |
| `books[]` | lista | Livros por código canônico (`GEN` a `REV`), capítulos e versículos. |
| `books[].chapters[].number` | inteiro positivo | Número do capítulo. |
| `books[].chapters[].verses[].number` | inteiro positivo | Número do versículo. |
| `books[].chapters[].verses[].text` | texto | Texto fornecido pela fonte autorizada; não pode estar vazio. |

O catálogo inicial reconhece os 66 livros da ordenação protestante em códigos
canônicos de três caracteres. Isso não decide qual cânon ou tradução o produto
adotará. Livros de outros cânones não podem ser importados até que o escopo seja
aprovado e o catálogo/esquema seja ampliado por migração.

O responsável também informa, por argumentos do comando, confirmação explícita
da licença, nome de quem revisou, data da revisão e URL HTTPS da evidência.
Esses dados são armazenados separadamente dos metadados fornecidos no JSON.

## Importação

Na raiz do projeto, use:

```powershell
python -m flask --app app:create_app import-scripture "C:\caminho\traducao.json" `
  --confirm-license `
  --reviewed-by "Nome do responsável" `
  --reviewed-at "AAAA-MM-DD" `
  --evidence-url "https://fonte-oficial.example/licenca"
```

Não execute esse comando sem revisar a licença para o uso específico.
HTTPS, URLs e confirmação manual são registros de proveniência, não prova
automática de autenticidade ou autorização jurídica. O importador não acessa a
internet e não verifica se o texto é uma transcrição fiel. Erros de validação
ou integridade cancelam a transação inteira.

O banco guarda o SHA-256 do arquivo exato importado e as contagens do lote.
Identificadores de tradução já existentes são rejeitados: não há sobrescrita
nem atualização incremental nesta etapa. Faça backup do banco antes de importar.

## Atribuir uma leitura a uma data

A aplicação não tem painel editorial nem distribui leituras automaticamente.
Depois de importar e revisar a tradução, uma pessoa responsável deve escolher
uma referência real e criar um registro em `daily_readings` para cada data e
tradução. A restrição do banco permite no máximo uma leitura por combinação
data/tradução e exige que os versículos inicial e final existam. A interface
também confere se todo o intervalo está completo antes de exibir qualquer
texto. Sem esse registro, a página mostra estado vazio.

Não há JSON de exemplo com versículos nesta documentação para evitar que um
exemplo inventado seja confundido com texto bíblico. O fixture de demonstração,
com textos rotulados como não bíblicos, existe exclusivamente nos testes.

## Reflexões e orações

A migração `003_reflections.sql` cria `reflections`, com um único registro por
`daily_readings`. Ela armazena:

- `title`, `reflection_text` e `simple_message`;
- `practical_application`, `prayer_text` e `reflection_question`;
- `author`, `status` (`draft` ou `published`) e `published_at`.

Todos os campos editoriais e a autoria são obrigatórios. Registros começam em
`draft`; uma publicação exige data/hora de publicação. A aplicação só consulta
itens `published`, vinculados a uma leitura de tradução aprovada e com intervalo
completo. O título da referência é derivado do registro de leitura, não digitado
na reflexão, para manter a associação consistente.

Ainda não existe painel editorial ou comando de publicação. A criação e revisão
dos registros deve ser feita por responsáveis autorizados, com conteúdo próprio
ou licenciado e referência previamente verificada. Não use textos sintéticos,
IA ou conteúdo não revisado como reflexão oficial; não inclua tradução bíblica
sem autorização adequada. Os conteúdos demonstrativos estão somente nos testes
e identificados explicitamente como não oficiais.
