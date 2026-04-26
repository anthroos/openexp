# User Stories — 5 MCP Tools (Hippocampus Model)

## Архітектурний принцип

**Гіпокамп:** записуй все → консолідуй (Q-values) → витягуй по запиту.

- **Write path** (автоматичний): SessionEnd hook інгестить кожну сесію в Qdrant
- **Read path** (по запиту): `/recall` скіл або `search_memory` MCP tool
- **Learn path** (фоновий): prediction → outcome → Q-value update

---

## Tool 1: `search_memory`

### User Story
> Як Claude Code, я хочу шукати в пам'яті за запитом з фільтрами,
> щоб знаходити релевантний досвід коли мені це ПОТРІБНО, а не коли
> система вирішить мені його підсунути.

### Хто викликає
- `/recall` скіл (основний споживач)
- SessionStart hook (broad context при старті)
- Claude напряму через MCP (рідко — коли CLAUDE.md каже "search before responding")

### Сценарії

**Сценарій 1: /recall з текстовим запитом**
```
Іван: /recall CLIENT_X contract terms
→ search_memory("CLIENT_X contract terms", limit=20)
→ Повертає ranked результати з різних сесій
→ Claude бачить: "2026-03-16: CLIENT_X $8000 fixed, 11-16 weeks..."
```

**Сценарій 2: /recall з фільтрами**
```
Іван: /recall --role user --date-from 2026-04-01 pipeline
→ search_memory("pipeline", role="user", date_from="2026-04-01")
→ Тільки що Іван казав за останній тиждень про pipeline
```

**Сценарій 3: /recall конкретна сесія**
```
Іван: /recall --session 5d060586
→ search_memory("", session_id="5d060586-...", limit=100)
→ Вся переписка з тієї сесії
```

**Сценарій 4: SessionStart (автоматично)**
```
Claude Code стартує в ~/openexp/
→ search_memory("openexp | Wednesday 2026-04-09", limit=10)
→ Top-10 інжектиться як additionalContext
```

### Acceptance Criteria
- [ ] Повертає results з hybrid score (semantic 50% + BM25 15% + recency 20% + importance 15%)
- [ ] Фільтри: role, session_id, source, date_from, date_to, type
- [ ] Latency < 500ms для limit=20
- [ ] Результати містять: memory text, score, session_id, timestamp, role

---

## Tool 2: `add_memory`

### User Story
> Як Claude Code, коли відбувається щось ВАЖЛИВЕ що не буде в
> транскрипті (рішення, зовнішній факт, інсайт), я хочу явно
> зберегти це в пам'ять з правильним типом і тегами.

### Хто викликає
- Claude через MCP (після рішень, коли Іван ділиться контекстом)
- CLAUDE.md правило: "after completing task → add_memory with outcome"

### Сценарії

**Сценарій 1: Рішення**
```
Іван: "ми вирішили не використовувати session heuristics для Q-values"
→ add_memory("Decision: no session heuristics for Q-values, only prediction→outcome", type="decision")
```

**Сценарій 2: Зовнішній факт**
```
Іван: "CLIENT_X бюджет збільшили до $12000"
→ add_memory("CLIENT_X budget increased to $12000", type="fact", client_id="comp-client_x")
```

**Сценарій 3: Outcome/результат**
```
Задача завершена
→ add_memory("Completed OpenExp v2 Stage 1: transcript ingest with idempotency", type="decision")
```

### Чого НЕ робить
- Не записує кожну дію (це робить SessionEnd через transcript ingest)
- Не дублює те що і так є в транскрипті
- Тільки те що має ВИЩУ importance ніж звичайна розмова

### Acceptance Criteria
- [ ] Зберігає в Qdrant з embedding
- [ ] Підтримує type: fact, decision, outcome
- [ ] Підтримує client_id для прив'язки до клієнтів
- [ ] Повертає ID нової пам'яті
- [ ] importance = 0.7 для decisions (вище ніж 0.5 для conversation)

---

## Tool 3: `memory_stats`

### User Story
> Як Іван, я хочу швидко перевірити стан системи пам'яті —
> скільки записів, чи працює Qdrant, чи є pending predictions —
> щоб розуміти чи все ок без залізання в логи.

### Хто викликає
- Іван через Claude: "покажи статистику пам'яті"
- Діагностика коли щось здається не так

### Сценарії

**Сценарій 1: Швидка перевірка**
```
Іван: memory_stats()
→ {
    "qdrant_points": 26424,
    "by_source": {"transcript": 26411, "decision": 13},
    "by_role": {"user": 12000, "assistant": 14411},
    "q_cache_entries": 0,
    "pending_predictions": 2,
    "sessions_stored": 156,
    "oldest_memory": "2026-02-15",
    "newest_memory": "2026-04-09"
  }
```

### Acceptance Criteria
- [ ] Показує count по source і role
- [ ] Показує pending predictions count
- [ ] Показує date range пам'ятей
- [ ] Відповідь < 3s
- [ ] Якщо Qdrant недоступний — повертає error, не падає

---

## Tool 4: `log_prediction`

### User Story
> Як Claude Code, коли я роблю прогноз або рекомендацію, я хочу
> записати його з confidence і memory_ids що вплинули, щоб потім
> порівняти з реальним результатом і система навчилась які
> пам'яті ведуть до правильних рішень.

### Хто викликає
- Claude через MCP коли робить prediction
- CLAUDE.md правило: "when making prediction → log_prediction"

### Сценарії

**Сценарій 1: Бізнес-прогноз**
```
Claude: "Думаю CLIENT_X підпише до п'ятниці"
→ log_prediction(
    prediction="CLIENT_X signs by Friday",
    confidence=0.7,
    memory_ids=["mem-client_x-call", "mem-client_x-proposal"]
  )
→ Returns: prediction_id="pred_abc123"
```

**Сценарій 2: Технічний прогноз**
```
Claude: "Idempotency guard має вирішити проблему дублікатів"
→ log_prediction(
    prediction="Idempotency guard prevents duplicates on re-ingest",
    confidence=0.9,
    memory_ids=["mem-about-duplicates"]
  )
```

**Сценарій 3: Поведінковий прогноз**
```
Claude: "Клієнт скоріш за все попросить знижку після demo"
→ log_prediction(
    prediction="Client requests discount after demo",
    confidence=0.5,
    memory_ids=["mem-client-history"]
  )
```

### Acceptance Criteria
- [ ] Зберігає prediction, confidence, memory_ids, timestamp
- [ ] Повертає prediction_id
- [ ] Prediction в стані "pending" до log_outcome
- [ ] memory_ids — це пам'яті що ВПЛИНУЛИ на прогноз (для reward)

---

## Tool 5: `log_outcome`

### User Story
> Як Claude Code або Іван, коли відомий реальний результат
> попереднього прогнозу, я хочу записати outcome з reward, щоб
> Q-values пам'ятей що вплинули на прогноз оновились — і система
> навчилась що працює а що ні.

### Хто викликає
- Claude через MCP коли дізнається результат
- Іван через Claude: "той прогноз про CLIENT_X — вони підписали"

### Сценарії

**Сценарій 1: Прогноз підтвердився**
```
Іван: "CLIENT_X підписали в середу"
→ log_outcome(
    prediction_id="pred_abc123",
    outcome="Signed Wednesday, $8000",
    reward=0.9
  )
→ Q-values пам'ятей ["mem-client_x-call", "mem-client_x-proposal"] оновлюються:
   q_action: 0.0 → 0.225 (0.0 + 0.25 * 0.9)
   q_hypothesis: 0.0 → 0.18
   q_fit: 0.0 → 0.225
→ Ці пам'яті тепер ранжуються ВИЩЕ при наступному /recall
```

**Сценарій 2: Прогноз провалився**
```
→ log_outcome(
    prediction_id="pred_def456",
    outcome="Duplicates still appeared after re-ingest",
    reward=-0.3
  )
→ Q-values пам'ятей знижуються
→ Ці пам'яті ранжуються НИЖЧЕ — система навчилась
```

**Сценарій 3: Нейтральний результат**
```
→ log_outcome(
    prediction_id="pred_ghi789",
    outcome="Client did not mention discount",
    reward=0.0
  )
→ Q-values не змінюються (reward=0)
```

### Ланцюг навчання (повний цикл)
```
1. /recall "CLIENT_X" → пам'яті з Q=0.0 (нові)
2. Claude робить прогноз → log_prediction(memory_ids=[...])
3. Результат відомий → log_outcome(reward=0.9)
4. Q-values оновлюються: 0.0 → 0.225
5. Наступний /recall "CLIENT_X" → ці пам'яті ранжуються ВИЩЕ
6. Кращий контекст → кращі рішення → кращі outcomes → ще вищі Q
```

### Acceptance Criteria
- [ ] Знаходить prediction по ID
- [ ] Оновлює Q-values всіх memory_ids з prediction (3 layers)
- [ ] Зберігає outcome в reward_log.jsonl
- [ ] Prediction стає "resolved"
- [ ] Це ЄДИНИЙ reward path у v2

---

## Що ВИДАЛЕНО (11 tools)

| Tool | Причина |
|------|---------|
| explain_q | Q=0 всюди, нічого пояснювати |
| calibrate_experience_q | Session reward видалено |
| protect_memory | Ніколи не використовувався |
| reload_q_cache | Внутрішня механіка |
| resolve_outcomes | CLI-only |
| experience_info | Один experience |
| experience_insights | Dead без session reward |
| experience_top_memories | Q=0 |
| reflect | Безкорисний output |
| memory_reward_history | reward_log порожній |
| reward_detail | Те саме |
| get_agent_context | Dead |
