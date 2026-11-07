# AutomaizerOnePiece

Aplicativo desktop para Windows 10/11 que monitora visualmente o Fortnite pela captura de tela.

**Estado atual: Etapa 6** — o aplicativo localiza a janela do jogo, reconhece a tela atual, acompanha cada
partida, lê o estado do esquadrão e os números do HUD, reconhece o token de recompensa, orienta a navegação pelos
menus, guarda o histórico em SQLite, encerra o monitoramento depois do token e aprende com as respostas Certo/Errado
do usuário. Falta a Etapa 7: testes integrados e o executável único.

## Instalação

Requer Python 3.12 ou superior, com o Tcl/Tk incluído no instalador oficial.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

## Execução

```powershell
.venv\Scripts\python main.py
```

Com a chave **Assistência** ligada, o monitoramento começa sozinho ao abrir o aplicativo. Desligada, clique em **Iniciar
monitoramento**. No jogo, use o modo de janela **Tela cheia em janela**: em tela cheia exclusiva, a captura pode retornar
quadros pretos.

## Testes

```powershell
.venv\Scripts\python -m pytest
```

Parte dos testes usa prints reais em `tests/fixtures/screens/`. Esses prints não vão para o controle de
versão, pois mostram nomes de outros jogadores; sem eles, esses testes são ignorados.

## Reconhecimento de telas

| Estado | Tela | Recortes em `assets/templates/` |
| --- | --- | --- |
| `LOBBY` | Lobby | `lobby/` |
| `MATCHMAKING` | Procurando partida | `matchmaking/` |
| `LOADING` | Carregando | `loading/` |
| `IN_MATCH` | Partida em andamento, do ônibus até o fim, em solo ou esquadrão | `in_match/` |
| `ELIMINATED` | Jogador eliminado, com chance de voltar: "Eliminado por" e modo espectador | `eliminated/` |
| `GAME_MENU` | Painel do menu aberto durante o jogo | `menu/` |
| `VICTORY` | Vitória | `victory/` |
| `DEFEAT` | Derrota definitiva: tela "Sua colocação" | `defeat/` |
| `MATCH_RESULT` | Resultado da partida | `result/` |
| `REWARD_CLAIM` | Recompensa para resgatar: tarefa concluída, passe de batalha, tesouro e outras | `reward_claim/` |
| `UNKNOWN` | Tela desconhecida | — |

Cada recorte é procurado no quadro inteiro, em tons de cinza, por correlação normalizada (OpenCV). O quadro
é reduzido a uma altura fixa antes da comparação, então a resolução do jogo não importa. Se nenhuma tela for
reconhecida, escalas alternativas são testadas periodicamente, cobrindo interfaces maiores ou menores.

Uma tela é reconhecida quando ao menos `min_matches` dos seus recortes alcançam o limite (`threshold`). Se
mais de uma for reconhecida, vale a primeira na ordem de `recognition.screens`, pois algumas aparecem
juntas: o resultado de uma vitória também mostra o banner, procurar partida também mostra as abas do lobby,
e o menu pode abrir sobre o modo espectador.

Uma mudança de tela só é aceita após `confirm_frames` capturas seguidas. Quadros não reconhecidos dentro de
uma tela conhecida, como com o mapa aberto, são tolerados por `unknown_grace_seconds`.

Durante a partida, se os recortes dela forem encontrados com folga sobre o limite, as demais telas não são
conferidas, pois com eles visíveis nenhuma outra tela está aberta. Isso reduz a análise de cerca de 250 ms
para cerca de 30 ms enquanto o jogo mais precisa do processador.

### Como adicionar um recorte

1. Tire um print em 1920x1080, com o jogo em **Tela cheia em janela**.
2. Recorte só o que nunca muda naquela tela: botões, textos fixos e ícones. Deixe de fora personagens,
   nomes de jogadores, números e o máximo possível do cenário.
3. Salve como PNG na pasta da tela e reinicie o aplicativo.

Os exemplos salvos são reavaliados automaticamente ao iniciar o monitoramento com recortes novos.

### Certo e Errado

Na página **Monitoramento**, a resposta vale para o quadro exibido, onde os recortes encontrados aparecem
marcados em dourado:

- **Certo** salva o quadro como exemplo da tela reconhecida.
- **Errado, era...** salva o quadro como exemplo da tela informada.

Com os exemplos, o aplicativo recalcula o limite de cada tela: baixa o limite se uma tela confirmada ficou
abaixo dele e sobe se outra tela foi reconhecida por engano. Os limites ficam entre `threshold_floor` e
`threshold_ceiling`, e nunca sobem mais que 8 pontos acima do valor configurado. Um ajuste só é feito se
resolver o caso, de modo que uma marcação errada sobre uma tela evidente não causa dano; quando não há o que
ajustar, o aplicativo avisa que a tela precisa de um recorte novo.

**Revisar exemplos salvos** permite conferir e apagar exemplos, o que desfaz o efeito deles. Os exemplos ficam
em `data/feedback/`. As respostas valem para o reconhecimento de telas, não para a leitura do esquadrão.

## Leitura do esquadrão

O HUD é lido durante a partida e o modo espectador, a partir da cor, da altura e do alinhamento das barras.
Nomes e skins não interferem.

| Estado do colega | Como aparece no jogo | O que o aplicativo mostra |
| --- | --- | --- |
| `ALIVE` | Barra verde, com ou sem escudo azul | Vida e escudo em %; "Vida baixa" abaixo do limite |
| `KNOCKED` | Barra vermelha | "Caído", precisa de ajuda |
| `ELIMINATED` | Barra azul grossa, que esvazia em 120 s | Tempo restante do cartão no chão |
| `ELIMINATED` | Sem barras, com o ícone do cartão | Cartão coletado, levar à van |
| `ELIMINATED` | Colega sumiu da lista | "Fora da partida" |

| Condição | Significado |
| --- | --- |
| `SQUAD_ACTIVE` | Há colegas de pé, ou o próprio usuário também está caído |
| `LAST_SQUAD_MEMBER_STANDING` | O usuário é o único de pé: os colegas estão caídos ou fora, e os caídos ainda podem voltar |
| `LAST_SQUAD_MEMBER_ALIVE` | Todos os colegas estão fora e o usuário segue vivo. Vale só para o esquadrão, não para a partida |
| `SQUAD_ELIMINATED` | Todos os integrantes estão fora |
| `NO_SQUAD` | Nenhum colega apareceu na partida, como no modo solo |
| `UNKNOWN` | Ainda não há leitura confiável |

Uma mudança só é aceita após `squad.confirm_frames` capturas seguidas. Quando uma linha some, são exigidas
`squad.missing_row_confirm_frames`, pois um objeto pode passar na frente dela. Com o menu do jogo aberto, o
quadro fica escurecido e o último estado confirmado é mantido.

### Limitações conhecidas

- O total de integrantes é o maior número visto na partida, e não um valor fixo de 4.
- Os colegas não são identificados pelo nome, pois não há leitura de texto: o painel mostra o recorte do jogo
  para que o usuário reconheça cada um. As linhas mudam de ordem quando o usuário é eliminado.
- A leitura supõe as cores e a posição padrão do HUD: modo para daltônicos, escala do HUD alterada ou HUD
  movido para fora do canto inferior esquerdo não são reconhecidos.

## Números da partida

Abates, assistências e jogadores restantes são lidos ao lado dos ícones do canto superior direito. Baús abertos vêm
da missão **Abra baús de tesouro**: a contagem é quanto o progresso dela subiu desde o início da partida.

| Número | Observação |
| --- | --- |
| Abates e assistências | Eliminado, o HUD pode mostrar os números do colega observado, então só valem com o usuário em jogo |
| Jogadores restantes | Continua sendo lido no modo espectador |
| Baús abertos | Aparece como "—" se a missão não estiver na tela. Baús abertos antes de ela aparecer não entram na conta |

Um número só é aceito depois de se repetir em 2 análises. Mudanças improváveis, como um salto de mais de 10 ou abates
diminuindo, precisam de 6 análises seguidas, o que corrige uma leitura errada sem deixá-la para sempre.

Os dígitos da missão e o texto dela ficam em `assets/templates/hud/` como modelos de forma: um PNG com canal alfa em que
só o desenho e o contorno escuro do caractere contam, de modo que o cenário atrás não interfere. Os dígitos e o texto da
missão foram medidos nos prints do usuário, exceto o 4, que nunca apareceu neles e veio de um vídeo de outro jogador:
os demais dígitos, tirados dos prints, leem esse mesmo vídeo corretamente, mas o 4 só será comprovado quando o progresso
passar a mostrá-lo na tela real.

O progresso é o que o jogo mostra na missão. Se o jogo somar nela os baús abertos pelos colegas, eles também entram.

## Token de recompensa

O token é procurado durante a própria partida, nunca assistindo a um colega, pois o aviso seria da recompensa
dele. Os recortes ficam em `assets/templates/rewards/event_token/`, e o prefixo do arquivo diz o que ele prova:

| Prefixo | Evidência |
| --- | --- |
| `nome_` | O nome da recompensa, como "Acesso à Loja Pirata". Sempre obrigatório |
| `icone_` | O ícone do token |
| `mensagem_` | A mensagem genérica, como "Você encontrou" |

Um quadro só conta para a confirmação com o nome e mais uma evidência, acima do nome e centralizada com ele.
São exigidos `rewards.confirm_frames` quadros assim, tolerando até `rewards.max_missed_frames` falhas seguidas
entre eles. O melhor quadro é salvo como evidência em `data/rewards/`, com data e hora. O registro persiste entre
execuções e não se duplica.

| Estado | Significado |
| --- | --- |
| `NOT_FOUND` | Nada encontrado |
| `CANDIDATE` | Algo parecido na tela, em confirmação |
| `CONFIRMED` | Token confirmado |
| `WAITING_FOR_MATCH_END` | Token obtido, aguardando o fim da partida atual |
| `STOPPED` | Token obtido e partida encerrada |

Se a confirmação não se completar, o melhor quadro fica guardado, e a página **Recompensas** pergunta se ele mostra
o token. O registro pode ser apagado na mesma página, para um token novo ou após uma confirmação indevida.

### Encerramento

Quando o token é confirmado, `token_obtained` é gravado e a partida atual continua sendo acompanhada, sem sugerir novas
partidas. Nesse momento sai também a mensagem no WhatsApp, se o aviso estiver ligado. Quando ela termina, `shutdown_pending` é gravado, o monitoramento para, `monitor_stopped` é gravado e aparece
a notificação **RECOMPENSA OBTIDA! Monitoramento finalizado.** O estado fica no banco: se o aplicativo for fechado em
qualquer um desses pontos, ele retoma dali ao abrir, e o monitoramento não pode ser iniciado até o registro ser apagado
em **Recompensas > Apagar registro do token**.

A busca só roda em quadros com dourado suficiente na região central, o que soma cerca de 140 ms a essas análises.

A mensagem "Você encontrou" foi recortada da tela real, de um item comum que usa o mesmo aviso. O nome e o ícone do
token vêm de um vídeo de outro jogador, ajustados ao tamanho medido nessa tela, por isso foram testados só em montagens.
Para trocá-los por recortes da própria tela, use a imagem salva em `data/rewards/` e guarde o PNG em 1920x1080, sem
ampliar. O nome é o que separa o token dos outros itens: "Dispositivo Elemental" tem o mesmo aviso e dá 44% nele.

## Assistente

A chave **Assistência**, no menu lateral, liga e desliga o mouse e as notificações, em todas as páginas. A escolha fica
guardada: ligada, ela volta ligada ao abrir o aplicativo, sem nenhum outro clique. Ela também inicia o monitoramento, sem
o qual o aplicativo não vê o jogo. Desligada, o aplicativo continua reconhecendo as telas e contando as partidas, mas não
move o cursor nem avisa.

A cada passo, o aplicativo leva o cursor até o botão, mostra um aviso ao lado dele e envia uma notificação com som. O
clique é sempre do usuário, percebido pela mudança da tela.

| Quando | Passos |
| --- | --- |
| O lobby aparece, a qualquer momento | **Jogar** |
| O usuário abre o menu durante a partida, inclusive assistindo a um colega | Aba de configurações, **Voltar ao Lobby**, **Jogar** |
| Só resta o usuário no esquadrão (`LAST_SQUAD_MEMBER_ALIVE`) | Abrir o menu, aba de configurações, **Voltar ao Lobby**, **Jogar** |
| A partida termina, em vitória ou derrota | **Jogar novamente** |
| Uma tela de recompensa aparece, inclusive sobre o lobby | **Resgatar**, ou **Resgatar tudo** com mais de uma recompensa |

Com colegas apenas caídos (`LAST_SQUAD_MEMBER_STANDING`), a saída não começa, pois eles ainda podem ser levantados.

### Recompensas

A tela de recompensa é reconhecida pelos botões, pois o título e o fundo mudam com a origem dela. O jogo só mostra
**Resgatar tudo** com mais de uma recompensa, então ele é procurado primeiro; sem ele, o cursor vai até **Resgatar**. O
aviso aparece acima do botão, pois ao lado ficaria sobre o texto dele ou sobre **Equipar**.

O clique é percebido quando a tela muda: o lobby aparece, o botão some por 3 análises seguidas, como na animação entre
uma recompensa e a seguinte, ou outro botão de resgatar aparece em outro lugar. A cada nova recompensa, o cursor vai de
novo até o botão. Depois da última, o passo seguinte é **Jogar**.

Os recortes vêm de prints da tela real: **Resgatar** com o fundo azul de uma tarefa e **Resgatar tudo** com o rosa do passe.
A comparação é em tons de cinza, e eles valem também para o botão creme do tesouro.

Regras que valem em todos os passos:

- O aplicativo não tem função de clique, não inicia partidas e não move o personagem.
- O cursor só se move com um menu confirmado na tela, nunca durante o jogo, porque giraria a câmera.
- O cursor se move uma vez por passo, para não disputar o mouse com o usuário.
- Se o botão não for reconhecido, o cursor não se move.
- Se o menu for fechado, o passo é interrompido. Abrir o menu de novo recomeça a sequência.
- Com o token obtido, nenhuma nova partida é sugerida.
- Levar o mouse a um canto da tela desliga a assistência até o próximo clique na chave, e o aplicativo avisa.
- A parada de emergência desliga a assistência e o monitoramento, e ela continua desligada ao reabrir.

### Apertar Esc

A opção **Apertar Esc por mim quando eu for o último vivo** vem desligada. Ligada, o aplicativo avisa, espera
`assistant.escape_delay_seconds` e aperta Esc uma vez por partida, só com o jogo em primeiro plano. Se um colega voltar
nesse intervalo, a tecla não é enviada. Se o menu não abrir, ou se já houve uma tecla nesta partida, o aplicativo pede que
o usuário o abra, pois uma segunda tecla poderia fechá-lo. Desligada, o aplicativo só pede que o usuário abra o menu.

Esta é a única entrada que o aplicativo envia ao jogo. Os termos do Fortnite não permitem automação, então usá-la pode
trazer risco para a conta, e o jogo pode ignorar a tecla. Sem esse risco, o Teclado Virtual do Windows (`osk`) ou o
Keyboard Manager do PowerToys substituem uma tecla Esc quebrada.

## Notificações

Cada notificação segue por três caminhos:

| Caminho | Observação |
| --- | --- |
| Som | Toca sempre, inclusive em jogo |
| Cartão do aplicativo | Aparece no canto direito de cada monitor por `notifications.card_seconds` |
| Notificação do Windows | Em jogo, o Windows a segura sem mostrar, por causa do modo Não incomodar durante jogos |

O cartão e o aviso ao lado do botão não recebem cliques, não tiram o foco do jogo e ficam fora de capturas de tela. Por
isso, eles também não aparecem em prints nem gravações, e não atrapalham o reconhecimento. Sobre o jogo, eles exigem o
modo **Tela cheia em janela**.

### Contar as notificações em outro programa

O arquivo `data/notifications.jsonl`, em UTF-8, recebe uma linha para cada notificação mostrada, no momento em que ela
aparece, e uma para cada clique feito. Ele só cresce, e é criado no primeiro registro:

```json
{"id": "3f2a9c41d7e84b0f9a6c1e5d2b7f8a10", "type": "click_required", "button": "PLAY", "message": "Clique em Jogar para iniciar outra partida", "created_at": "2026-11-05T21:14:03.512-03:00", "app": "AutomaizerOnePiece"}
```

| `type` | Significado |
| --- | --- |
| `click_required` | Notificação que pede um clique. Cada lembrete do mesmo clique gera outra linha |
| `info` | Notificação que não pede clique |
| `reward_obtained` | Notificação de token obtido e monitoramento finalizado |
| `click_done` | O usuário deu o clique pedido, percebido pela mudança da tela. Não é uma notificação |

`button` traz `MENU_TAB`, `LEAVE_MATCH`, `PLAY`, `PLAY_AGAIN`, `CLAIM` ou `CLAIM_ALL` em `click_required` e `click_done`, e
`null` nos demais.

Para contar as notificações, conte as linhas `click_required`, `info` e `reward_obtained`. Um clique que o aplicativo não
pediu, como **Retornar ao lobby**, não é registrado.

O próprio aplicativo já conta os cliques feitos na página **Cliques**, descrita a seguir.

Para um programa fora deste PC, preencha o webhook em **Configurações > Notificações**: cada registro é enviado por POST,
com o mesmo JSON no corpo e a senha no cabeçalho `X-Automaizer-Secret`, se houver. O envio roda em uma thread própria, com
3 tentativas, e nunca atrasa a análise do jogo. Se todas falharem, o registro não é reenviado depois.

## Contador de cliques

Integrado do Projeto click no mouse, que deixa de ser necessário. A página **Cliques**, no menu lateral, mostra o total,
o último clique e os cliques contados desde que o app abriu, com o botão **Zerar contagem**.

Cada linha `click_done` em `data/notifications.jsonl` soma 1, uma vez por `id`: são os cliques que o assistente pediu e
o usuário deu. Pedidos de clique, lembretes e avisos não contam. O contador fica à parte do resto do aplicativo, em
`click_counter/`: ele só lê esse arquivo, em uma thread própria, sempre que o app está aberto, monitorando ou não. Com
**Configurações > Notificações > Gravar em arquivo** desligado, nada novo é contado, e a página avisa.

O estado fica em `data/click_counter.json`, no mesmo formato do `state.json` do projeto original: caminho do arquivo,
posição de leitura, total e os últimos 1000 ids. Copiar aquele `state.json` para cá continua a contagem dele.

- Na primeira execução, ou se o arquivo de notificações mudar de lugar, a leitura começa do fim dele.
- Zerar não muda a posição de leitura nem os ids guardados, então nenhum clique antigo é contado de novo.
- Uma linha ainda sendo escrita espera o fim dela; arquivo travado pelo OneDrive é lido no ciclo seguinte.
- Um estado ilegível é guardado como `click_counter.danificado-<data>.json`, e a contagem recomeça do zero.

## Aviso no WhatsApp

Quando o token é encontrado, o app manda uma mensagem no seu WhatsApp. A página **WhatsApp**, no menu lateral, tem a
chave **Avisar no WhatsApp quando o token for encontrado**, o serviço, o seu número com `+` e o código do país, como
`+55 11 91234-5678`, e o botão **Enviar teste**, que salva e manda uma mensagem na hora. A página mostra o resultado do
último envio, que também aparece nos eventos.

A mensagem sai uma vez por token, quando ele é confirmado. Reabrir o app não a manda de novo, e um token novo só avisa
depois de **Recompensas > Apagar registro do token**. O envio roda em uma thread própria, com 3 tentativas se não houver
internet ou se o serviço estiver fora do ar; uma chave ou um token errado não é tentado de novo.

O número, a chave e o token ficam em `data/whatsapp.json`, fora do `config.json`, que vai para o git. Em outro PC,
preencha a página de novo. O código fica à parte, em `whatsapp/`, e só recebe o aviso do token.

### CallMeBot

Grátis para uso pessoal e o mais simples: só o número e uma chave. Ele só manda mensagens para o número que pediu a
chave.

1. Abra a [página do CallMeBot](https://www.callmebot.com/blog/free-api-whatsapp-messages/) e salve nos contatos o
   número do bot que aparece lá.
2. Do seu WhatsApp, mande para esse contato: `I allow callmebot to send me messages`.
3. A resposta traz `Your APIKEY is ...`: coloque a chave na página **WhatsApp** e use **Enviar teste**.

É um serviço pequeno, de uma pessoa só. Em novembro de 2026 a página dizia que o bot estava cheio, sem vagas para
números novos, e há relatos de mensagens que pararam de chegar por um tempo. Se a chave não chegar em 2 minutos, o
próprio CallMeBot diz para tentar de novo depois de 24 horas.

### API oficial do WhatsApp (Meta)

Dá mais trabalho, mas é o serviço oficial. O número de teste que a Meta cria não pede forma de pagamento e manda para
até 5 números cadastrados.

1. Em [Meta for Developers](https://developers.facebook.com/apps), crie um app com o caso de uso do WhatsApp e siga o
   [guia de início](https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started).
2. Na página **Configuração da API** (API Setup), copie a identificação do número de teste (Phone number ID), cadastre
   o seu número no campo **Para** e confirme com o código que chega no WhatsApp.
3. Copie o token da mesma página. Ele vale 24 horas e serve para testar. Para deixar ligado, crie um usuário do sistema
   em **Usuários do sistema** (System users), nas [configurações do negócio](https://business.facebook.com/latest/settings),
   dê a ele o app e a conta do WhatsApp e gere um token sem validade com as permissões `whatsapp_business_messaging` e
   `whatsapp_business_management`.
4. Preencha a página **WhatsApp** e use **Enviar teste**.

Fora de uma conversa aberta, a API só manda modelos aprovados. O padrão é o `hello_world`, em `en_US`, que toda conta
nova já tem, e ele chega em inglês. Para uma mensagem em português, crie um modelo no
[WhatsApp Manager](https://business.facebook.com/latest/whatsapp_manager/message_templates), como
`token_encontrado` em Português (BR), categoria Utilidade, com um texto sem variáveis, como "O AutomaizerOnePiece
encontrou o token de recompensa.". Depois que ele for aprovado, troque o nome e o idioma (`pt_BR`) na página. O teste
manda o mesmo modelo.

### Quando a mensagem não chega

| Na página | O que fazer |
| --- | --- |
| A chave não confere com o número | Confira os dois: a chave vale só para o número que a pediu |
| O token expirou ou está errado | Gere outro; o da Configuração da API vale 24 horas |
| O seu número não está na lista do número de teste | Cadastre-o no campo **Para** da Configuração da API |
| Não há modelo aprovado com esse nome nesse idioma | Confira o nome e o idioma do modelo no WhatsApp Manager |
| Sem resposta do serviço | Sem internet ou serviço fora do ar, depois de 3 tentativas |
| Enviada, mas não chegou | O serviço aceitou a mensagem e falhou depois. No CallMeBot, mande `Resume` para o bot: é o que ele recomenda quando para de entregar |

Com número do Brasil, se tudo parecer certo e mesmo assim falhar, tente o número sem o 9 depois do DDD: algumas contas
do WhatsApp usam esse formato.

## Histórico

A página **Histórico** lista as partidas com data, duração, resultado e números. Ao escolher uma, mostra os integrantes
e os eventos dela. O **Dashboard** soma as partidas de todas as sessões e mostra à parte as da sessão atual.

Os dados ficam em `data/monitor.db`:

| Tabela | Conteúdo |
| --- | --- |
| `matches` | Início, fim, duração, resultado, situação do usuário e do esquadrão, token, abates, assistências, baús e jogadores restantes |
| `squad_members` | Último estado de cada integrante da partida; o número 1 é o usuário |
| `rewards` | Uma linha por tipo de recompensa: estado, confiança, data, evidência e a partida onde apareceu |
| `events` | Eventos com a partida, o tipo, a descrição, a confiança e a data |
| `monitor_control` | Uma linha com `token_obtained`, `shutdown_pending` e `monitor_stopped` |

Uma partida deixada aberta por um aplicativo fechado às pressas é encerrada sem resultado ao abrir. Uma partida da qual
o usuário sai vivo também fica como **Sem resultado**, pois não houve tela de vitória nem de derrota. Falhas ao gravar
vão para o log e não interrompem o monitoramento. Um arquivo danificado é guardado como `monitor.danificado-<data>.db`,
e um novo é criado; nesse caso, o registro do token e o histórico recomeçam vazios, e um aviso aparece nos eventos.

## Estados da captura

| Estado | Significado |
| --- | --- |
| `STOPPED` | Monitoramento parado |
| `SEARCHING` | Janela do jogo não encontrada; a busca continua |
| `MINIMIZED` | Janela minimizada ou sem área visível |
| `BACKGROUND` | Jogo atrás de outra janela; não captura, pois o quadro mostraria a outra janela |
| `CAPTURING` | Capturando a área interna da janela |
| `ERROR` | Falha ao capturar; nova tentativa a cada ciclo |

## Configuração

O arquivo `config.json` pode ser editado na página **Configurações** ou diretamente; alterações diretas valem
ao reiniciar. Um arquivo inválido é preservado como `config.json.invalid-<data>` e os valores padrão são
restaurados. O aviso no WhatsApp tem configuração à parte, em `data/whatsapp.json`.

| Chave | Descrição |
| --- | --- |
| `capture.fps` | Quadros capturados por segundo |
| `capture.window_poll_seconds` | Intervalo de busca enquanto a janela não é encontrada |
| `capture.capture_only_when_foreground` | Pausa a captura quando o jogo não está em primeiro plano |
| `capture.black_frame_threshold` | Brilho médio abaixo do qual o quadro é tratado como preto |
| `capture.process_names` | Executáveis reconhecidos como o jogo (critério principal) |
| `capture.window_titles` | Títulos usados apenas se o processo não puder ser consultado |
| `recognition.fps` | Análises por segundo |
| `recognition.confirm_frames` | Capturas seguidas necessárias para aceitar uma mudança de tela |
| `recognition.unknown_grace_seconds` | Tempo tolerado sem reconhecer a tela antes de mudar para `UNKNOWN` |
| `recognition.working_height` | Altura à qual o quadro é reduzido para a comparação |
| `recognition.scales` | Escalas testadas; a primeira é a usada normalmente |
| `recognition.threshold_floor`, `recognition.threshold_ceiling` | Faixa permitida para os limites ajustados |
| `recognition.screens.<ESTADO>.folder` | Subpasta de `assets/templates/` com os recortes da tela |
| `recognition.screens.<ESTADO>.threshold` | Limite de confiança da tela, antes dos ajustes |
| `recognition.screens.<ESTADO>.min_matches` | Quantos recortes diferentes precisam ser encontrados |
| `squad.confirm_frames` | Capturas seguidas necessárias para aceitar uma mudança no esquadrão |
| `squad.missing_row_confirm_frames` | Capturas seguidas necessárias para dar um colega como fora da partida |
| `squad.low_health_percent` | Vida a partir da qual um colega é tratado como precisando de ajuda |
| `squad.card_threshold` | Limite de confiança do ícone de cartão coletado |
| `rewards.reward_type` | Tipo registrado para a recompensa |
| `rewards.folder` | Subpasta de `assets/templates/` com os recortes do token |
| `rewards.icon_threshold`, `rewards.name_threshold`, `rewards.message_threshold` | Limite de confiança de cada evidência |
| `rewards.candidate_threshold` | A partir de quanto o nome da recompensa já conta como algo parecido |
| `rewards.confirm_frames`, `rewards.max_missed_frames` | Quadros exigidos e falhas toleradas entre eles |
| `rewards.scales` | Tamanhos procurados; 1.0 é o medido com o HUD padrão em 1080p |
| `rewards.search_region` | Onde procurar: esquerda, topo, direita e base, de 0 a 1 |
| `rewards.gold_gate` | Parcela mínima de pixels dourados para que a busca seja feita |
| `assistant.enabled` | A chave **Assistência**: mouse e notificações ligados ao abrir o aplicativo |
| `assistant.press_escape` | Apertar Esc quando o usuário for o último vivo; desligado por padrão |
| `assistant.escape_delay_seconds` | Espera entre o aviso e a tecla |
| `assistant.menu_timeout_seconds` | Tempo até pedir que o usuário abra o menu, se ele não abrir |
| `assistant.button_threshold` | Confiança mínima para mover o cursor até um botão |
| `assistant.reminder_seconds`, `assistant.max_reminders` | Intervalo e quantidade de lembretes do clique |
| `assistant.cursor_move_seconds` | Duração do movimento do cursor |
| `notifications.file_enabled` | Grava as notificações e os cliques feitos em `data/notifications.jsonl` |
| `notifications.webhook_url` | Endereço `http://` ou `https://` que recebe os mesmos registros; vazio desliga o envio |
| `notifications.webhook_secret` | Senha enviada no cabeçalho `X-Automaizer-Secret` |
| `notifications.card_seconds` | Tempo do cartão na tela |
| `ui.appearance_mode` | `dark`, `light` ou `system` |
| `ui.preview_fps` | Atualizações por segundo da visualização |
| `logging.level` | `DEBUG`, `INFO`, `WARNING` ou `ERROR` |
| `logging.max_bytes`, `logging.backup_count` | Rotação dos arquivos em `logs/` |

## Estrutura

```
main.py                     Ponto de entrada
config.json                 Configurações do usuário
core/
  application.py            Fachada usada pela interface; início, parada e emergência
  history_recorder.py       Grava partidas, integrantes e eventos em uma thread própria, sem atrasar a análise
  shutdown_manager.py       Encerramento depois do token, persistido entre execuções
  capture.py                Localização da janela e thread de captura
  recognition_service.py    Thread que analisa os quadros capturados
  state_machine.py          Estados do jogo e confirmação das mudanças de tela
  config.py                 Modelos Pydantic, leitura e gravação do config.json
  event_manager.py          Eventos entre threads, entregues na thread da interface
  logging_setup.py          Logs com rotação
  paths.py                  Caminhos em desenvolvimento e no executável
recognition/
  interface_detector.py     Comparação do quadro com os recortes de cada tela
  match_detector.py         Início, fim e resultado de cada partida
  squad_detector.py         Leitura das barras do esquadrão no HUD
  hud_reader.py             Leitura de abates, assistências, jogadores restantes e missão de baús
  survival_detector.py      Condições do esquadrão e confirmação das leituras
  reward_detector.py        Reconhecimento do token, evidências e registro
  feedback.py               Exemplos Certo/Errado e ajuste dos limites
assistant/
  guided_state_machine.py   Sequência de passos e regras de quando agir
  navigation_controller.py  Localiza os botões e executa os passos
  mouse_guidance.py         Movimento do cursor e tecla Esc; não tem função de clique
  user_confirmation.py      Espera pelo clique e lembretes
  notifier.py               Notificação do Windows com som
  notification_outbox.py    Arquivo e webhook que contam a outros programas os cliques já feitos
click_counter/
  counter.py                Contador de cliques, integrado do Projeto click no mouse; só lê notifications.jsonl
whatsapp/
  notifier.py               Aviso do token no WhatsApp, pelo CallMeBot ou pela API oficial da Meta
database/
  database.py               Conexão com o SQLite e criação das tabelas
  models.py                 Registros guardados no banco
  repositories.py           Consultas e gravações de cada tabela
ui/
  app_window.py             Janela, menu lateral com a chave Assistência e ciclo de atualização
  dashboard.py              Visão geral
  squad_panel.py            Integrantes do esquadrão, com o recorte de cada um
  monitoring.py             Visualização, confiança, transições e respostas Certo/Errado
  feedback_review.py        Revisão dos exemplos salvos
  rewards.py                Token detectado, evidência, data e encerramento
  assistant.py              Próxima ação, botão identificado e opção do Esc
  guidance_overlay.py       Avisos sobre o jogo: ao lado do botão e cartão de notificação
  settings.py               Edição validada das configurações
  history.py                Partidas anteriores, integrantes e eventos
  click_counter.py          Página Cliques: total, último clique e zerar contagem
  whatsapp.py               Página WhatsApp: serviço, número, teste e resultado do último envio
  theme.py, widgets.py, formatting.py    Identidade visual e componentes
assets/templates/           Recortes de referência de cada tela
tests/                      Testes unitários e de reconhecimento
```

## Etapas

- [x] 1. Estrutura do projeto, interface e captura de tela
- [x] 2. Reconhecimento de telas e máquina de estados
- [x] 3. Reconhecimento do esquadrão e sobrevivência
- [x] 4. Detecção do token de recompensa
- [x] 5. Assistente de navegação
- [x] 6. Integração SQLite, histórico e encerramento
- [ ] 7. Testes integrados e geração do executável
