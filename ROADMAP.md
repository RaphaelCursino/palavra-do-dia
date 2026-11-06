# Palavra do Dia — planejamento e roadmap

## Atualização — Etapa 6: fonte canônica BLIVRE

- [x] O importador e os testes usam exclusivamente
  `data/imports/blivre/porbr2018_usfm.zip`.
- [x] A importação foi validada em banco SQLite temporário vazio com 66 livros,
  1.189 capítulos, 31.102 versículos e 1.290 notas.
- [x] A integridade do banco temporário foi confirmada sem violações de chaves
  estrangeiras.
- [x] As cópias duplicadas na raiz e os 66 arquivos USFM soltos foram removidos
  após a validação do novo caminho.

## 1. Visão do projeto

**Palavra do Dia** será um aplicativo cristão de leitura e reflexão bíblica, responsivo e em português. A experiência principal é apresentar uma passagem bíblica identificada por livro, capítulo e versículos, oferecer uma reflexão associada à passagem e permitir que a pessoa guarde conteúdos e acompanhe planos de leitura.

O conteúdo deve ser confiável e rastreável: nenhum texto inventado, aleatório ou apresentado como Escritura. A tradução e a origem de cada texto precisam estar identificadas, e sua licença deve permitir o uso pretendido.

### Objetivos iniciais

- Consultar a leitura do dia e navegar para as referências bíblicas relacionadas.
- Ler reflexões claramente diferenciadas do texto bíblico.
- Favoritar passagens e acompanhar planos de leitura.
- Funcionar bem em celulares e computadores.
- Manter uma base técnica pequena, documentada e gratuita para desenvolver e testar localmente.

### Fora do escopo inicial proposto

- Rede social, comentários públicos ou publicação de conteúdo por usuários.
- Recomendações geradas por IA, seleção aleatória de versículos ou conteúdo não revisado.
- Aplicativos nativos, notificações push e integrações pagas.
- Contas, sincronização entre dispositivos e operação multiusuário, até que esse escopo seja aprovado.

## 2. Planejamento de funcionalidades

### MVP

1. **Página inicial e leitura diária**
   - Exibir uma passagem associada à data local configurada para o aplicativo.
   - Mostrar referência, tradução e texto autorizado.
   - Ter estados claros para conteúdo indisponível e erro de carregamento; nunca substituir a passagem por texto fictício.
2. **Leitura bíblica**
   - Navegar por livro, capítulo e passagem.
   - Validar referências e informar quando não houver conteúdo disponível na tradução selecionada.
3. **Reflexões**
   - Exibir título, texto, autoria ou responsável editorial, data e passagem relacionada.
   - Diferenciar visualmente e semanticamente reflexão e texto bíblico.
4. **Favoritos**
   - Adicionar e remover passagens favoritas e consultar a lista.
   - Definir antes da implementação se os favoritos são locais ao dispositivo ou associados a uma conta.
5. **Planos de leitura**
   - Consultar planos publicados, seus dias e referências.
   - Iniciar um plano, marcar leituras como concluídas e consultar o progresso.
   - Não marcar como concluída uma leitura apenas por abrir a tela.
6. **Acessibilidade e responsividade**
   - Navegação por teclado, foco visível, rótulos acessíveis, contraste adequado e suporte a ampliação.
   - Layout adaptável para telas pequenas e grandes, sem depender de gestos exclusivos.
7. **Estados de interface**
   - Prever carregamento, vazio, sucesso, validação e falha, com mensagens úteis e sem dados simulados.

### Depois do MVP, sujeito a validação

- Contas e sincronização entre dispositivos.
- Busca avançada, anotações pessoais, lembretes e modo de leitura offline.
- Mais traduções, idiomas ou conteúdo editorial.
- Painel administrativo para publicar e revisar conteúdo.

## 3. Arquitetura proposta

**Monólito modular**, adequado ao MVP e simples de operar:

- **Interface:** HTML semântico, CSS responsivo e JavaScript para interações pontuais. Templates renderizados pelo Flask; evitar um framework de frontend até haver necessidade comprovada.
- **Aplicação web:** Python com Flask, responsável por rotas, validação e composição de respostas.
- **Serviços:** regras de negócio para leitura diária, referências, favoritos, reflexões e progresso de planos.
- **Persistência:** SQLite acessado por uma camada de repositórios; consultas parametrizadas e migrações versionadas.
- **Conteúdo editorial:** importação controlada de dados aprovados, separada do código da aplicação.
- **Testes:** testes unitários das regras e testes de integração das rotas e do banco, usando banco temporário.

Fluxo sugerido: navegador → rota Flask → serviço de domínio → repositório → SQLite. A interface não deve acessar o banco diretamente. A camada de serviços também valida disponibilidade e licença do conteúdo antes de sua publicação.

### Decisões de operação

- Começar com execução local e ferramentas gratuitas.
- Usar configurações por ambiente; não versionar segredos.
- SQLite é suficiente para desenvolvimento e uma instalação de baixo volume. Se o produto exigir múltiplas instâncias ou concorrência elevada, avaliar banco cliente-servidor em uma etapa própria.
- O modo de publicação, hospedagem e eventual necessidade de contas deve ser aprovado antes de expor a aplicação a múltiplos usuários.

## 4. Estrutura de pastas proposta

```text
palavra-do-dia/
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── routes/
│   │   ├── pages.py
│   │   └── api.py
│   ├── services/
│   │   ├── daily_reading.py
│   │   ├── favorites.py
│   │   └── reading_plans.py
│   ├── repositories/
│   ├── templates/
│   └── static/
│       ├── css/
│       ├── js/
│       └── images/
├── data/
│   ├── imports/
│   └── README.md
├── migrations/
├── tests/
│   ├── unit/
│   └── integration/
├── instance/                 # banco e configuração local; não versionar
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
└── ROADMAP.md
```

Os módulos são uma proposta inicial, não arquivos a criar nesta etapa. A estrutura pode ser ajustada após a aprovação do escopo e da fonte de conteúdo.

## 5. Banco de dados proposto

Modelo relacional inicial, com chaves estrangeiras habilitadas e restrições para evitar duplicidades.

| Tabela | Finalidade e campos principais |
|---|---|
| `translations` | Identidade da tradução, idioma, detentor dos direitos, licença/escopo, atribuição, responsável/data/evidência da revisão e situação de aprovação. |
| `books` | Catálogo de livros, código, nome, testamento e ordem. O catálogo implementado contém os 66 livros na ordenação protestante; a escolha do cânon do produto segue pendente. |
| `chapters` | Livro e número do capítulo, com unicidade por livro/capítulo. |
| `verses` | Tradução, capítulo, número do versículo, texto e lote de importação; chave única por tradução/capítulo/versículo. |
| `import_batches` | Origem e URL, data de aquisição, SHA-256 do arquivo, versão do importador, data de importação e contagens do lote. |
| `daily_readings` | Data, tradução e intervalo editorial de versículos; unicidade por data/tradução e chaves estrangeiras para os versículos inicial/final. |
| `reflections` | Título, corpo, autoria, data, estado editorial e vínculo com a leitura ou referência. |
| `favorites` | Passagem/referência favoritada, data de criação e proprietário conforme decisão entre dispositivo e conta. |
| `reading_plans` | Título, descrição, autoria, duração e estado de publicação. |
| `reading_plan_days` | Plano, número do dia e referência(s) de leitura. |
| `plan_enrollments` | Plano iniciado e proprietário, se o progresso for persistido por pessoa/dispositivo. |
| `plan_progress` | Inscrição, dia ou leitura e data de conclusão; restrição contra duplicar conclusão. |

As migrações 001 e 002 implementam `translations`, `books`, `chapters`,
`verses`, `import_batches`, `daily_readings` e o controle de versões. A
atribuição à data continua editorial: não existe leitura cadastrada no banco
real. As demais tabelas são propostas para etapas futuras e não foram criadas.

### Regras de integridade e privacidade

- Referências devem apontar para livros e passagens existentes quando o texto completo estiver disponível.
- Texto bíblico e reflexão são registros distintos.
- Datas e fuso horário do versículo diário devem ser definidos explicitamente; não derivar a passagem de aleatoriedade.
- Decidir, antes da implementação, se favoritos e progresso pertencem a uma instalação anônima ou a contas. Não compartilhar dados pessoais entre visitantes.
- Não guardar dados pessoais desnecessários. Caso contas sejam aprovadas, definir autenticação, retenção e exclusão de dados como parte do desenho.

## 6. Textos bíblicos, direitos e proveniência

1. **Escolher a tradução antes de importar qualquer texto.** Confirmar por escrito a licença aplicável ao texto integral, armazenamento em banco, exibição na web, redistribuição, território, limites de citação e atribuições obrigatórias.
2. **Preferência por fonte gratuita e com permissão clara**, como uma tradução em domínio público cuja situação jurídica tenha sido verificada para o território de uso. “Gratuito para ler” não significa automaticamente “livre para copiar e redistribuir”.
3. Se a tradução escolhida não autorizar armazenamento integral, não copiar seu texto para o banco. Avaliar uma API oficial ou licenciada, observando seus termos, limites, atribuição, disponibilidade e permissão de cache. Um endpoint acessível não é, por si só, autorização de uso.
4. Registrar para cada conjunto importado a tradução, a fonte, a data de obtenção, a licença/termos consultados, a atribuição requerida e a versão do processo de importação.
5. Importar apenas conteúdo verificável e aprovado; validar referências, cobertura e integridade antes de publicar. Manter a origem associada ao conteúdo.
6. Reflexões e planos são conteúdo editorial próprio ou devidamente licenciado, com autoria e revisão definidas. Não apresentar paráfrases ou textos editoriais como versículos.
7. Se os direitos forem incertos, bloquear a importação e a publicação até esclarecimento. Não usar scraping nem contornar restrições.

**Gate obrigatório:** a tradução e o método de obtenção/armazenamento dependem da decisão do responsável. A Bíblia Livre (BLIVRE), edição porbr2018, foi explicitamente solicitada e aprovada para esta instalação sob CC BY 4.0; outras traduções continuam bloqueadas até verificação e aprovação próprias.

## 7. Versículo diário, reflexões, favoritos e planos

- **Versículo diário:** selecionar uma referência previamente revisada e atribuí-la a cada data por um calendário editorial. O serviço consulta a data no fuso configurado e busca o texto na tradução aprovada. Não gerar conteúdo aleatório. Para data sem publicação, informar indisponibilidade.
- **Reflexões:** publicar apenas conteúdo editorial revisado, com referência, autoria e data; preservar a distinção visual entre citação bíblica e comentário.
- **Favoritos:** criar/remover e listar com persistência. A associação do registro (conta ou instalação) depende da decisão de identidade e privacidade tomada antes da fase de dados.
- **Planos:** armazenar dias e referências como conteúdo editorial. O progresso registra ações explícitas do usuário, permite retomar e não altera as datas do plano sem uma regra aprovada.
- **Fuso e calendário:** escolher fuso padrão e comportamento de troca de dia; testar limites de meia-noite e mudanças de horário.

## 8. Interface responsiva

### Telas iniciais

- **Início:** leitura do dia, referência, tradução, ação de favoritar e acesso à reflexão.
- **Leitura:** navegação entre livros/capítulos e apresentação legível do texto.
- **Reflexão:** conteúdo, autoria e referência associada.
- **Favoritos:** lista, estado vazio e remoção.
- **Planos:** catálogo, detalhes, leituras do plano e progresso.

### Diretrizes

- Prioridade a celular, com coluna de leitura confortável e navegação simples; em telas amplas, limitar a largura do texto.
- HTML semântico e componentes reutilizáveis sem excesso de JavaScript.
- Contraste, foco visível, uso por teclado, rótulos para controles e suporte a leitores de tela.
- Não depender apenas de cor para indicar estado; respeitar preferência por movimento reduzido.
- Definir visualmente tradução, referência e reflexão para que não sejam confundidas.
- Testar tamanhos de tela e estados de conteúdo vazio, longo, indisponível e com erro.

## 9. Etapas de desenvolvimento e critérios de validação

Cada etapa exige validação explícita antes de começar a seguinte. Os critérios abaixo são a proposta para testar cada entrega; não autorizam avançar automaticamente.

| Etapa | Entrega | Como validar | Gate |
|---|---|---|---|
| 0. Aprovação do planejamento | Escopo, decisões pendentes e este roadmap revisados | Aprovar funcionalidades, abordagem de usuários e operação, tradução e critérios | Aprovação do usuário |
| 1. Fundação do projeto (concluída) | Aplicação Flask mínima, configuração centralizada, conexão SQLite vazia, página provisória, erros 404/500, documentação e testes automatizados | 5 testes passaram; Flask iniciou; página inicial conferida no navegador; configuração SQLite inválida produz erro claro; nenhum dado fictício | Validação explícita do usuário antes da Etapa 2 |
| 2. Banco e importador (base técnica aprovada; conteúdo real bloqueado) | Esquema versionado, catálogo inicial de livros, importador offline, consultas e documentação | 16 testes passaram à época; fixture sintético não bíblico importado somente em testes; migração aplicada; banco sem tradução, capítulos ou versículos reais; base técnica e cânon aprovados pelo usuário | Fonte, tradução e licença continuam pendentes para conteúdo real |
| 3. Início e experiência de leitura diária (implementada) | Interface responsiva, data no fuso aprovado, seletor de traduções aprovadas, consulta de intervalo diário e compartilhamento com atribuição | 29 testes passaram à época; consultas por data/tradução, intervalo íntegro e ausência testados com fixture explicitamente não bíblico; interface conferida em desktop e celular; banco não recebeu conteúdo nessa etapa | Etapa concluída antes da aprovação da tradução |
| 4. Reflexões e orações (implementada; sem conteúdo editorial real) | Migração aditiva, consulta de conteúdo publicado, reflexão integrada à leitura diária, histórico paginado, detalhe com aplicação, oração copiável e pergunta para refletir | 39 testes passaram; estados vazios, publicação, autoria, associação e referência cobertos; cópia da oração exercitada no navegador; home, histórico e detalhe conferidos em telas estreita e ampla; seletor de tradução ajustado para caber em celular | Validada pelo início da Etapa 5; conteúdo editorial real continua sujeito a autoria, revisão e autorização |
| 5. Favoritos (implementada; isolamento por navegador) | Salvar/remover versículos e reflexões, listagem filtrável, estados vazios e associação por identidade anônima de sessão | 116 testes passaram na suíte atual; inclui ações, filtros, duplicação, CSRF, integridade referencial e isolamento entre dois clientes independentes | Não há contas nem sincronização em nuvem |
| 6. Meu Progresso (implementada) | Calendário local, conclusão/reversão de leitura diária válida, histórico, estatísticas de sequência e meta semanal | 75 testes passaram; migração aditiva 005, fuso America/Sao_Paulo, prevenção de duplicidade/integridade e estados vazios cobertos; rotas verificadas; interface conferida em telas amplas e estreitas sem rolagem horizontal | Aguardar validação explícita do usuário antes da Etapa 7 |
| Conteúdo inicial: BLIVRE (implementado) | Importador do ZIP USFM na raiz, hash fixado, proveniência/licença, navegação por livro/capítulo, notas e integração com favoritos/leitura diária | ZIP raiz conferido como porbr2018 (66 arquivos USFM e hash verificado); reimportação confirmada idempotente sem alterar qualquer registro; 66 livros, 1.189 capítulos, 31.102 versículos e 1.290 notas; 82 testes passaram, compileall e integridade conferidos | Não há leitura diária editorial cadastrada; a Etapa 7 não foi iniciada |
| Palavra e Reflexão do Dia (conteúdo editorial inicial) | Sete leituras/reflexões originais vinculadas à BLIVRE, home, histórico, favoritos e compartilhamento | Referências conferidas contra versículos BLIVRE; publicação transacional/idempotente, home e histórico cobertos por testes; fuso America/Sao_Paulo; sem alterar o esquema | Conteúdo cobre 5–11/11/2026; novas semanas exigem cadastro editorial próprio; a Etapa 7 não foi iniciada |
| Redesign da seção Bíblia | Catálogo visual dos 66 livros separado por testamento, busca tolerante a acentos, páginas de livro e capítulo redesenhadas, navegação anterior/próxima, controles de fonte, modo foco, compartilhamento de capítulo, favoritos e atribuição mantidos | Teste de integração confirma 39 livros no Antigo Testamento, 27 no Novo, abertura/navegação, licença e favoritos; navegador confirmou busca “joao”, ausência de resultados, compartilhamento, controles de fonte e foco, navegação entre capítulos e ausência de rolagem horizontal em desktop/celular; suíte completa executada após a implementação | Nenhuma alteração de banco ou do texto bíblico; aprimoramentos restritos a templates, CSS e JavaScript |
| Recursos pessoais integrados | Meta semanal e progresso aprimorados, anotações bíblicas, marcações por cor/categoria, diário de oração e preferências persistentes de leitura | Migração aditiva `007_personal_features.sql`; CRUD, validações, CSRF, filtros, persistência e compatibilidade cobertos pela suíte atual; banco temporário verificado com `PRAGMA integrity_check` e `foreign_key_check`; isolamento entre visitantes validado | Não há contas nem sincronização em nuvem; a identidade é anônima e específica do navegador |
| Isolamento por visitante (implementado) | Identificador aleatório seguro em sessão persistente, proprietário validado no servidor, cookies protegidos e segredo obrigatório em produção | Dois clientes independentes receberam dados distintos; tentativas cruzadas de leitura, edição e exclusão retornaram `404`; persistência após reinício, `HttpOnly`, `SameSite=Lax`, modo `Secure` em produção e ausência de segredo foram testados; banco original preservado | Limpar cookies ou trocar de navegador inicia um novo espaço pessoal; não há recuperação de identidade |
| Redesign do modo escuro | Paleta azul-marinho, superfícies e controles harmonizados, leitura bíblica legível, destaques preservados e cor de tema do navegador ajustada | Suíte completa com 106 testes, `compileall` e `node --check`; nove rotas verificadas em desktop e viewport de 390 px, sem rolagem horizontal ou superfícies brancas; modo claro conferido sem alteração do fundo original | Alteração restrita a CSS e metadado visual; preferências continuam locais à instalação |
| Redesign da navegação superior | Alinhamento uniforme dos links, ícones consistentes, menu Meu Espaço com ações identificadas, seta, animação, fechamento externo e navegação por teclado; engrenagem no título de Configurações | 106 testes aprovados, `compileall` e `node --check`; menu testado por clique, setas, Escape e clique externo; alinhamento desktop e viewport de 390 px verificado sem rolagem horizontal; banco preservado | Alteração restrita à navegação compartilhada e ao título de Configurações |
| Experiência de leitura e busca bíblica | Busca por referência e palavra na BLIVRE, resultados com acesso direto ao versículo, seletores rápidos de livro/capítulo, navegação entre livros e capítulos e controles de leitura preservados | 108 testes aprovados; buscas válidas, vazias e inválidas testadas; Gênesis 50 → Êxodo 1 confirmado; busca e abertura de João 3:16 validadas no navegador; desktop e viewport de 390 px sem rolagem horizontal; `compileall`, `node --check`, integridade SQLite e chaves estrangeiras conferidos | Sem migração ou alteração do conteúdo bíblico; resultados limitados aos 50 primeiros versículos correspondentes |
| 7. Planos de leitura | Catálogo, detalhe, início e progresso | Leituras ordenadas, progresso explícito e persistente, reinício/retomada conforme regra aprovada | Aprovar comportamento dos planos |
| 8. Acessibilidade e responsividade | Ajustes de interface e documentação | Testes de teclado, foco, contraste, leitor de tela e telas móveis/desktop | Aprovar experiência antes do lançamento |
| 9. Preparação para publicação (preparada; sem deploy) | Entrada WSGI para `create_app()`, dependências de produção separadas, segredo por ambiente, caminho persistente configurável para SQLite, proteção de arquivos privados e instruções PythonAnywhere | 116 testes aprovados; `compileall` e `node --check` aprovados; WSGI iniciou com segredo de teste e recusou segredo ausente; banco original conferido com integridade `ok`, zero chaves estrangeiras inválidas e contagens preservadas; arquivos sensíveis auditados e não enviados | Deploy, criação de conta, backup operacional e restauração em hospedagem ainda exigem validação específica |

## 10. Ferramentas e custos

- Python, Flask, SQLite, HTML, CSS e JavaScript: ferramentas gratuitas e adequadas ao desenvolvimento local.
- Usar ambiente virtual e dependências declaradas/versionadas.
- Priorizar testes e ferramentas gratuitas; não assumir serviço de hospedagem, API bíblica ou conteúdo pago.
- Hospedagem e serviços externos só serão escolhidos se necessários, após verificar custo, termos, privacidade e disponibilidade.

## 11. Decisões para aprovação antes do conteúdo real

1. Aprovar ou ajustar o escopo do MVP e os itens fora de escopo.
2. Modelo aprovado para esta etapa pelo usuário: favoritos persistidos na instalação local atual, sem autenticação; `owner_key` deixa espaço para uma etapa futura de usuários, sem implementar contas/sincronização.
3. BLIVRE porbr2018, fonte e licença CC BY 4.0 aprovadas explicitamente pelo usuário para esta importação; qualquer tradução adicional exige aprovação própria.
4. Fuso do versículo diário definido pelo usuário: `America/Sao_Paulo`.
5. Seleção de tradução definida pelo usuário: lista dinâmica das traduções aprovadas com leitura cadastrada para a data.
6. Confirmar que cada etapa será apresentada para teste e aprovação antes da próxima.
7. Cânon definido pelo usuário: protestante, 66 livros. Outros cânones exigiriam nova decisão e ampliação do catálogo por migração.

**Estado atual:** Etapas 1–6 estão implementadas conforme validações registradas. A Etapa 4 inclui a migração `003_reflections.sql`, conteúdo editorial publicado consultável, histórico e detalhe. A Etapa 5 usa a migração aditiva `004_favorites.sql` para salvar/remover e filtrar versículos e reflexões; favoritos são locais, sem autenticação. A Etapa 6 usa a migração `005_reading_progress.sql` para calendário, conclusões de leituras válidas, estatísticas, histórico e meta semanal com datas em `America/Sao_Paulo`. A implementação BLIVRE usa a migração aditiva `006_verse_notes.sql`, o ZIP USFM porbr2018 localizado na raiz e a licença CC BY 4.0. A semana editorial contém sete leituras validadas no banco BLIVRE e sete reflexões originais para 5–11/11/2026. A seção Bíblia recebeu um redesign de apresentação sem alteração do banco ou do conteúdo: os 66 livros estão agrupados por testamento e podem ser pesquisados; páginas de livros/capítulos incluem navegação, controles de fonte, modo foco e compartilhamento, mantendo favoritos e créditos. A atualização integrada de recursos pessoais usa a migração aditiva `007_personal_features.sql` para anotações, marcações, diário e preferências; a meta semanal e o progresso também foram aprimorados. O modo escuro recebeu uma revisão visual completa de paleta, superfícies, formulários, cards, leitura bíblica, destaques, estados e responsividade, sem alterar o tema claro ou o banco. A navegação superior foi refinada com alinhamento uniforme, ícones, menu Meu Espaço acessível e título de Configurações com engrenagem. A etapa final adicionou cartões de versículos em Canvas/PNG e conquistas baseadas em conclusões reais, com a migração aditiva `008_achievements.sql`, página própria e resumo no progresso. A suíte atual tem 116 testes aprovados, `compileall` e `node --check` passaram, o banco original mantém 66 livros, 1.189 capítulos, 31.102 versículos e 1.290 notas, e as novas rotas foram exercitadas no navegador em desktop e 390 px sem rolagem horizontal. Permanecem fora do escopo contas, sincronização, novas traduções sem licença confirmada e publicação editorial automática.
