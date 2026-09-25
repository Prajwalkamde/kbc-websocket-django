(function () {
  const code = window.ROOM_CODE;
  const session = localStorage.getItem("player_session") || "";

  let socket = null;
  let reconnectTimer = null;
  let fallbackTimer = null;
  let timerHandle = null;
  let state = null;

  const $ = id => document.getElementById(id);

  const money = n =>
    "₹" + Number(n || 0).toLocaleString("en-IN");

  const wsUrl = () =>
    `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/rooms/${code}/`;

  function show(msg) {
    const el = $("player-message");
    if (!el) return;

    el.textContent = msg;
    el.classList.remove("hidden");

    setTimeout(() => {
      el.classList.add("hidden");
    }, 3200);
  }



  /* =========================================================
     WEBSOCKET
  ========================================================= */

  function connect() {
    if (!code || !session) {
      show("Player session missing. Please join again.");
      return;
    }

    clearTimeout(reconnectTimer);

    try {
      socket?.close();
    } catch (_) {}

    socket = new WebSocket(wsUrl());

    socket.onopen = () => {
      socket.send(
        JSON.stringify({
          type: "auth",
          role: "player",
          token: session
        })
      );

      clearInterval(fallbackTimer);
    };

    socket.onmessage = e => {
      try {
        const m = JSON.parse(e.data);

        /*
         * No console.log of full payloads: every room state broadcast is
         * logged per player, and with 200 players that floods the console
         * and slows the page down.
         */

        if (
          m.type === "authenticated" ||
          m.type === "player.private"
        ) {
          applyState(m.state);
        }

        else if (m.type === "room.state") {
          applyPublic(m.state);
        }

        else if (m.type === "action.ok") {
          applyAction(m.action, m.result);
        }

        else if (
          m.type === "error" ||
          m.type === "auth_error"
        ) {
          show(m.error);
        }

      } catch (err) {
        console.error("WebSocket message error:", err);
      }
    };

    socket.onclose = () => {
      fallbackFetch();
      reconnectTimer = setTimeout(connect, 2000);
    };

    socket.onerror = () => {
      try {
        socket.close();
      } catch (_) {}
    };
  }

  function send(action, body = {}) {
    if (
      !socket ||
      socket.readyState !== WebSocket.OPEN
    ) {
      show("Reconnecting…");
      return;
    }

    socket.send(
      JSON.stringify({
        type: "action",
        action,
        ...body
      })
    );
  }

  async function fallbackFetch() {
    try {
      const r = await fetch(
        `/api/rooms/${code}/state/`,
        {
          headers: {
            "X-Player-Session": session
          }
        }
      );

      const d = await r.json();

      if (d.ok) {
        applyState(d.state);
      }

    } catch (_) {}

    clearInterval(fallbackTimer);

    fallbackTimer = setInterval(
      fallbackFetch,
      5000
    );
  }

  /* =========================================================
     ACTION RESPONSE
  ========================================================= */

  function answerBelongsToCurrentQuestion(answer, s) {
    if (!answer || !s?.question) return false;
    if (answer.question_id != null) {
      return Number(answer.question_id) === Number(s.question.id);
    }
    if (answer.question_number != null) {
      return Number(answer.question_number) === Number(s.current_question);
    }
    return true;
  }

  function emptyAnswer(s) {
    return {
      attempts: [],
      locked: false,
      selected: null,
      correct: null,
      question_id: s?.question?.id ?? null,
      question_number: s?.current_question ?? null
    };
  }

  function applyAction(action, result) {
    if (!state) return;

    if (action === "answer") {

      const resultQuestionId = result.question_id;
      const resultQuestionNumber = result.question_number;

      if (
        resultQuestionId != null &&
        state.question?.id != null &&
        Number(resultQuestionId) !== Number(state.question.id)
      ) {
        return;
      }

      if (
        resultQuestionNumber != null &&
        state.current_question != null &&
        Number(resultQuestionNumber) !== Number(state.current_question)
      ) {
        return;
      }

      state.my_answer = {
        attempts: result.attempts || [],
        locked: Boolean(result.locked),
        selected: result.selected || null,
        correct: result.correct ?? null,
        question_id: resultQuestionId ?? state.question?.id ?? null,
        question_number: resultQuestionNumber ?? state.current_question ?? null
      };

      render(state);
    }

    else if (action === "fastest_submit") {

      state.fastest =
        state.fastest || {};

      state.fastest.submitted = true;

      state.fastest.submitted_correct =
        result.correct;

      state.fastest.submitted_answer =
        result.answer ||
        state.fastest.submitted_answer;

      render(state);
    }

    else if (action === "lifeline") {

      state.me =
        state.me || {
          lifelines: {}
        };

      state.me.lifelines =
        state.me.lifelines || {};

      if (result.lifeline) {
        state.me.lifelines[
          result.lifeline
        ] = false;
      }

      if (result.payload?.removed) {
        state.removed_options =
          result.payload.removed;
      }

      if (result.payload?.question) {
        state.question =
          result.payload.question;
      }

      render(state);
    }

    else if (action === "audience_vote") {

      if (state.active_poll) {
        state.active_poll.can_vote = false;
      }

      render(state);
    }

    else if (action === "expert_answer") {

      if (state.expert_incoming) {
        state.expert_incoming.answered =
          true;
      }

      render(state);
    }
  }

  /* =========================================================
     PUBLIC ROOM STATE
     
     IMPORTANT:
     Reset question-specific player state whenever
     current question changes.
  ========================================================= */

  function applyPublic(s) {
    if (!s) return;

    if (!state) {
      state = s;
      render(state);
      return;
    }

    const previousQuestion =
      Number(state.current_question || 0);

    const incomingQuestion =
      Number(s.current_question || 0);

    const questionChanged =
      previousQuestion !== incomingQuestion;

    const previousQuestionId =
      state.question?.id ??
      state.question?.pk ??
      state.question?.started_at ??
      state.question?.text ??
      null;

    const incomingQuestionId =
      s.question?.id ??
      s.question?.pk ??
      s.question?.started_at ??
      s.question?.text ??
      null;

    const questionObjectChanged =
      previousQuestionId !== null &&
      incomingQuestionId !== null &&
      String(previousQuestionId) !== String(incomingQuestionId);

    const becameActiveQuestion =
      s.status === "QUESTION_ACTIVE" &&
      state.status !== "QUESTION_ACTIVE" &&
      state.status !== "PAUSED";

    const isNewQuestion =
      questionChanged ||
      questionObjectChanged ||
      becameActiveQuestion;

    const previousFastestPrompt =
      state.fastest?.prompt?.text || null;

    const incomingFastestPrompt =
      s.fastest?.prompt?.text || null;

    const fastestRoundChanged =
      previousFastestPrompt &&
      incomingFastestPrompt &&
      previousFastestPrompt !== incomingFastestPrompt;

    const keepAnswer =
      !isNewQuestion &&
      answerBelongsToCurrentQuestion(state.my_answer, s);

    state = {
      ...state,
      ...s,
      me: state.me,
      my_answer: keepAnswer ? state.my_answer : emptyAnswer(s),
      removed_options: isNewQuestion ? [] : (state.removed_options || []),
      active_poll: isNewQuestion ? null : state.active_poll,
      expert: isNewQuestion ? null : state.expert,
      expert_incoming: isNewQuestion ? null : state.expert_incoming,
      fastest: fastestRoundChanged
        ? { ...(s.fastest || {}) }
        : { ...(state.fastest || {}), ...(s.fastest || {}) }
    };

    if (
      s.status === "FASTEST_FINGER" ||
      s.status === "LOBBY"
    ) {
      state.my_answer = emptyAnswer(s);
      state.removed_options = [];
    }

    render(state);
  }

  /* =========================================================
     PRIVATE STATE
  ========================================================= */

  function applyState(s) {
    if (!s) return;

    /*
     * Private state is authoritative.
     * Drop any answer that does not belong to the current question.
     */
    if (s.question && !answerBelongsToCurrentQuestion(s.my_answer, s)) {
      s = { ...s, my_answer: emptyAnswer(s) };
    }

    state = s;
    render(state);
  }

  /* =========================================================
     MAIN RENDER
  ========================================================= */

  function render(s) {
    if (!s) return;

    if ($("my-name")) {
      $("my-name").textContent =
        s.me?.name || "";
    }

    if ($("my-prize")) {
      $("my-prize").textContent =
        money(s.me?.prize);
    }

    if ($("q-num")) {
      $("q-num").textContent =
        `${s.current_question || 0} / ${s.total_questions}`;
    }

    /*
     * QUESTION
     */
    if (s.question) {

      $("category").textContent =
        `${s.question.category} · ${s.question.difficulty}`;

      $("question").textContent =
        s.question.text;

      if (!answerBelongsToCurrentQuestion(s.my_answer, s)) {
        s.my_answer = emptyAnswer(s);
      }
      const myAnswer = s.my_answer;

      const attempts =
        myAnswer.attempts || [];

      const locked =
        Boolean(myAnswer.locked);

      const revealed =
        Boolean(s.question.correct_option);

      const wrong =
        revealed &&
        myAnswer.selected &&
        myAnswer.correct === false;

      const boundQuestionId = s.question.id;
      const boundQuestionNumber = s.current_question;

      KBCDom.render(
        $("player-options"),
        Object.entries(
          s.question.options || {}
        )
        .map(([k, v]) => {

          const disabled =
            locked ||
            attempts.includes(k) ||
            s.status !== "QUESTION_ACTIVE";

          const selectedClass =
            attempts.includes(k)
              ? (
                  revealed
                    ? (
                        myAnswer.correct
                          ? "selected-correct"
                          : (
                              wrong &&
                              myAnswer.selected === k
                                ? "selected-wrong"
                                : "selected"
                            )
                      )
                    : "selected"
                )
              : "";

          const removed =
            s.removed_options?.includes(k);

          const button = KBCDom.el(
            "button",
            {
              class: `option ${selectedClass} ${removed ? "removed" : ""}`.trim(),
              dataset: { option: k },
              disabled: Boolean(disabled || removed)
            },
            [
              KBCDom.el("b", { text: `${k}.` }),
              " ",
              v
            ]
          );

          button.addEventListener("click", () => {

            if (
              button.disabled ||
              state.status !== "QUESTION_ACTIVE"
            ) {
              return;
            }

            if (
              state.question?.id !== boundQuestionId ||
              Number(state.current_question) !== Number(boundQuestionNumber)
            ) {
              return;
            }

            $("player-options")
              .querySelectorAll("[data-option]")
              .forEach(o =>
                o.classList.remove("selected")
              );

            button.classList.add("selected");

            send("answer", {
              option: button.dataset.option
            });
          });

          return button;
        })
      );

      renderLifelines(
        s.me?.lifelines || {}
      );

      /*
       * Reveal result
       */
      if (s.question.correct_option) {

        $("result").classList.remove("hidden");

        const ok =
          myAnswer.selected ===
          s.question.correct_option;

        KBCDom.render($("result"), [
          KBCDom.el("b", { text: ok ? "✅ CORRECT" : "❌ WRONG" }),
          " — Correct answer: ",
          String(s.question.correct_option)
        ]);

      } else {

        $("result").classList.add("hidden");
      }

    } else {

      /*
       * No question currently.
       */
      if ($("player-options")) {
        KBCDom.clear($("player-options"));
      }

      if ($("result")) {
        $("result").classList.add("hidden");
      }
    }

    renderFinalResults(
      s.final_results
    );

    renderPoll(s);
    renderExpert(s);

    startTimer(s);

    renderFastest(
      s.fastest,
      s.status
    );
  }

  /* =========================================================
     LIFELINES
  ========================================================= */

  function renderLifelines(lifelines) {

    document
      .querySelectorAll(".life")
      .forEach(button => {

        button.classList.toggle(
          "used",
          lifelines[button.dataset.life] === false
        );

        /*
         * Also disable physically.
         */
        button.disabled =
          lifelines[button.dataset.life] === false;
      });
  }

  /* =========================================================
     FINAL RESULTS
  ========================================================= */

  function renderFinalResults(rows) {

    const panel =
      $("final-results");

    const body =
      $("final-results-body");

    if (!panel || !body) return;

    if (!rows?.length) {
      panel.classList.add("hidden");
      KBCDom.clear(body);
      return;
    }

    panel.classList.remove("hidden");

    KBCDom.render(body, [
      KBCDom.table(
        ["Player", "Score", "Correct Questions", "Wrong Questions", "Total Time"],
        rows.map(x => [
          x.name,
          money(x.score),
          x.correct?.length ? x.correct.join(", ") : "None",
          x.wrong?.length ? x.wrong.join(", ") : "None",
          `${(Number(x.time_ms || 0) / 1000).toFixed(2)} s`
        ])
      )
    ]);
  }

  /* =========================================================
     AUDIENCE POLL
  ========================================================= */

  function renderPoll(s) {

    const p = s.active_poll;
    const box = $("poll-box");

    if (!box) return;

    if (!p) {
      box.classList.add("hidden");
      return;
    }

    box.classList.remove("hidden");

    if (
      p.requester &&
      p.completed
    ) {

      KBCDom.render(box, [
        KBCDom.el("b", { text: "Audience Poll Result" }),

        KBCDom.el(
          "div",
          { class: "stats" },
          "ABCD".split("").map(x =>
            KBCDom.el("div", { class: "stat" }, [
              KBCDom.el("b", { text: x }),
              KBCDom.el("br"),
              `${Number(p.percentages?.[x] || 0)}%`
            ])
          )
        )
      ]);

    }

    else if (p.can_vote) {

      KBCDom.render(box, [
        KBCDom.el("b", { text: "Audience Poll" }),

        KBCDom.el("p", {
          text: `${p.requester_name || "Another player"} needs your vote.`
        }),

        KBCDom.el(
          "div",
          { class: "options-grid" },
          "ABCD".split("").map(x => {

            const button =
              KBCDom.el("button", { class: "btn", text: x });

            button.addEventListener("click", () =>
              send("audience_vote", {
                poll_id: p.id,
                option: x
              })
            );

            return button;
          })
        )
      ]);

    }

    else {

      KBCDom.render(box, [
        KBCDom.el("b", { text: "Audience Poll active" }),
        KBCDom.el("p", { text: "Waiting for other players…" })
      ]);
    }
  }

  /* =========================================================
     EXPERT
  ========================================================= */

  function renderExpert(s) {

    const box = $("expert-box");

    if (!box) return;

    if (
      s.expert &&
      !s.expert.answered
    ) {

      box.classList.remove("hidden");

      KBCDom.render(box, [
        KBCDom.el("b", { text: "Expert selected:" }),
        " ",
        s.expert.expert_name,
        KBCDom.el("br"),
        "Waiting for their answer…"
      ]);

      return;
    }

    if (s.expert?.answer) {

      box.classList.remove("hidden");

      KBCDom.render(box, [
        KBCDom.el("b", {
          text: `${s.expert.expert_name} answered:`
        }),
        " ",
        s.expert.answer
      ]);

      return;
    }

    if (
      s.expert_incoming &&
      !s.expert_incoming.answered
    ) {

      box.classList.remove("hidden");

      KBCDom.render(box, [
        KBCDom.el("b", { text: "You are the Expert" }),

        KBCDom.el("p", {
          text: s.expert_incoming.question?.text || ""
        }),

        KBCDom.el(
          "div",
          { class: "options-grid" },
          "ABCD".split("").map(x => {

            const button =
              KBCDom.el("button", { class: "btn", text: x });

            button.addEventListener("click", () =>
              send("expert_answer", {
                request_id: s.expert_incoming.request_id,
                option: x
              })
            );

            return button;
          })
        )
      ]);

      return;
    }

    box.classList.add("hidden");
  }

  /* =========================================================
     FASTEST FINGER
  ========================================================= */

  function renderFastest(ff, status) {

    const panel = $("fastest");

    if (!panel) return;

    const visible =
      Boolean(ff?.started) &&
      status === "FASTEST_FINGER";

    panel.classList.toggle(
      "hidden",
      !visible
    );

    if (!visible) {
      return;
    }

    if (!ff.prompt) {
      return;
    }

    $("ff-text").textContent =
      ff.prompt.text || "";

    const submittedAnswer =
      JSON.stringify(
        ff.submitted_answer || []
      );

    KBCDom.render(
      $("ff-items"),
      (ff.prompt.options || [])
        .map((option, index) => {

          const selected =
            ff.submitted &&
            JSON.stringify(option) ===
              submittedAnswer;

          const resultClass =
            selected
              ? (
                  ff.submitted_correct
                    ? "submitted"
                    : "submitted-wrong"
                )
              : "";

          const button = KBCDom.el(
            "button",
            {
              class: `fastest-choice ${resultClass}`.trim(),
              dataset: { fastestOption: String(index) },
              disabled: Boolean(ff.locked || ff.submitted)
            },
            [
              KBCDom.el("b", { text: `${index + 1}.` }),
              " ",
              (option || []).join(" → ")
            ]
          );

          button.addEventListener("click", () => {

            if (
              ff.locked ||
              ff.submitted
            ) {
              return;
            }

            const answer =
              ff.prompt.options[
                Number(button.dataset.fastestOption)
              ];

            $("ff-items")
              .querySelectorAll(
                "[data-fastest-option]"
              )
              .forEach(b =>
                b.classList.remove(
                  "selected"
                )
              );

            button.classList.add(
              "selected"
            );

            send("fastest_submit", {
              answer
            });
          });

          return button;
        })
    );
  }

  /* =========================================================
     TIMER
  ========================================================= */

  function startTimer(s) {

    clearInterval(timerHandle);

    const el = $("player-timer");
    if (!el) return;

    const status = s?.status;
    el.classList.toggle("paused", status === "PAUSED");

    if (status === "PAUSED") {
      const remaining = s.paused_remaining_seconds;
      el.textContent = remaining == null
        ? "PAUSED"
        : String(Math.max(0, Number(remaining)));
      return;
    }

    if (status !== "QUESTION_ACTIVE" || !s.deadline) {
      el.textContent = "—";
      return;
    }

    const tick = () => {
      el.textContent = Math.max(
        0,
        Math.ceil((new Date(s.deadline) - Date.now()) / 1000)
      );
    };

    tick();
    timerHandle = setInterval(tick, 200);
  }

  /* =========================================================
     LIFELINE CLICK
  ========================================================= */

  document
    .querySelectorAll(".life")
    .forEach(button => {

      button.onclick = () => {

        if (
          button.classList.contains("used") ||
          button.disabled
        ) {
          return;
        }

        send("lifeline", {
          lifeline:
            button.dataset.life
        });
      };
    });

  /* =========================================================
     START
  ========================================================= */

  connect();

})();
