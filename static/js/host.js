(function () {

  const codeFromPath =
    location.pathname
      .split("/")
      .filter(Boolean)
      .pop();

  let roomCode =
    codeFromPath &&
    codeFromPath !== "host"
      ? codeFromPath.toUpperCase()
      : "";

  let hostSession =
    localStorage.getItem("host_session") || "";

  let socket = null;
  let reconnectTimer = null;
  let fallbackTimer = null;
  let timerHandle = null;

  let soundOn = true;
  let audioContext = null;
  let previousStatus = "";
  let lastState = null;

  /*
   * Prevent accidental double-clicks.
   */
  let actionInProgress = false;

  const $ = id =>
    document.getElementById(id);

  const money = n =>
    "₹" +
    Number(n || 0)
      .toLocaleString("en-IN");

  const wsUrl = () =>
    `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/rooms/${roomCode}/`;

  /* =========================================================
     SOUND
  ========================================================= */

  function playTone(
    frequency,
    duration = .12,
    delay = 0
  ) {

    if (!soundOn) return;

    const AC =
      window.AudioContext ||
      window.webkitAudioContext;

    if (!AC) return;

    audioContext =
      audioContext || new AC();

    const start =
      audioContext.currentTime +
      delay;

    const oscillator =
      audioContext.createOscillator();

    const gain =
      audioContext.createGain();

    oscillator.type = "sine";

    oscillator.frequency.value =
      frequency;

    gain.gain.setValueAtTime(
      .0001,
      start
    );

    gain.gain.exponentialRampToValueAtTime(
      .055,
      start + .015
    );

    gain.gain.exponentialRampToValueAtTime(
      .0001,
      start + duration
    );

    oscillator
      .connect(gain)
      .connect(audioContext.destination);

    oscillator.start(start);

    oscillator.stop(
      start + duration + .02
    );
  }

  function playCue(name) {

    if (name === "start") {
      playTone(392, .14);
      playTone(523, .18, .12);
    }

    if (name === "reveal") {
      playTone(523, .12);
      playTone(659, .18, .1);
    }

    if (name === "end") {
      playTone(659, .14);
      playTone(523, .14, .12);
      playTone(392, .22, .24);
    }

    if (name === "click") {
      playTone(260, .06);
    }
  }

  /* =========================================================
     MESSAGE
  ========================================================= */

  function show(msg) {

    const el =
      $("host-message");

    if (!el) return;

    el.textContent = msg;

    el.classList.remove(
      "hidden"
    );

    setTimeout(() => {
      el.classList.add(
        "hidden"
      );
    }, 3500);
  }



  /* =========================================================
     CREATE ROOM
  ========================================================= */

  async function create() {

    try {

      const r =
        await fetch(
          "/api/rooms/create/",
          {
            method: "POST",
            headers: {
              "X-CSRFToken":
                window.CSRF_TOKEN
            }
          }
        );

      const d =
        await r.json();

      if (!d.ok) {
        throw new Error(d.error);
      }

      roomCode =
        d.room_code;

      hostSession =
        d.host_session;

      localStorage.setItem(
        "host_session",
        hostSession
      );

      $("room-code").textContent =
        roomCode;

      history.replaceState(
        {},
        "",
        `/host/${roomCode}/`
      );

      connect();

    } catch (e) {

      show(e.message);
    }
  }

  /* =========================================================
     WEBSOCKET
  ========================================================= */

  function connect() {

    if (
      !roomCode ||
      !hostSession
    ) {
      return;
    }

    clearTimeout(
      reconnectTimer
    );

    try {
      socket?.close();
    } catch (_) {}

    socket =
      new WebSocket(
        wsUrl()
      );

    socket.onopen = () => {

      socket.send(
        JSON.stringify({
          type: "auth",
          role: "host",
          token: hostSession
        })
      );

      clearInterval(
        fallbackTimer
      );
    };

    socket.onmessage = e => {

      try {

        const m =
          JSON.parse(e.data);

        /*
         * No console.log of full payloads here: with 200 players the host
         * receives a large host state frequently, and serializing/logging
         * every one of them is what froze the host UI.
         */

        if (
          m.type === "host.state" ||
          m.type === "authenticated"
        ) {

          actionInProgress = false;
          render(m.state);
        }

        else if (
          m.type === "host.delta"
        ) {

          renderDelta(
            m.state
          );
        }

        else if (
          m.type === "error" ||
          m.type === "auth_error"
        ) {

          actionInProgress = false;

          const btn = document.querySelector(
            '[data-action="start_fastest"]'
          );

          if (btn && btn.disabled) {
            btn.disabled = false;
            btn.textContent = "Start Fastest Finger";
          }

          show(m.error);
        }

      } catch (err) {

        console.error(
          "Host WebSocket error:",
          err
        );
      }
    };

    socket.onclose = () => {

      fallbackFetch();

      reconnectTimer =
        setTimeout(
          connect,
          2000
        );
    };

    socket.onerror = () => {

      try {
        socket.close();
      } catch (_) {}
    };
  }

  /* =========================================================
     SEND ACTION
  ========================================================= */

  function send(action, body = {}) {

    if (
        !socket ||
        socket.readyState !== WebSocket.OPEN
    ) {
        show("Reconnecting…");
        return false;
    }

    if (actionInProgress) {
        return false;
    }

    if (action === "start_fastest") {
        actionInProgress = true;
    }

    socket.send(
        JSON.stringify({
            type: "action",
            action,
            ...body
        })
    );

    if (action !== "start_fastest") {
        setTimeout(() => {
            actionInProgress = false;
        }, 250);
    }

    return true;
}

  /* =========================================================
     FALLBACK API
  ========================================================= */

  async function fallbackFetch() {

    if (!roomCode) return;

    try {

      const r =
        await fetch(
          `/api/rooms/${roomCode}/state/`,
          {
            headers: {
              "X-Host-Session":
                hostSession
            }
          }
        );

      const d =
        await r.json();

      if (d.ok) {
        render(d.state);
      }

    } catch (_) {}

    clearInterval(
      fallbackTimer
    );

    fallbackTimer =
      setInterval(
        fallbackFetch,
        5000
      );
  }

  /* =========================================================
     PLAYERS (throttled DOM rebuild)
  ========================================================= */

  /*
   * With 200 players every full host state carries 200 rows. Join/disconnect
   * churn therefore rebuilds the list at most once per 400 ms (leading +
   * trailing throttle). The counter itself is a single text node and is
   * updated immediately, so it always reflects the full joined total from
   * player_count — never a stale partial list length.
   */
  const PLAYER_LIST_INTERVAL_MS = 400;
  let playerListTimer = null;
  let playerListPending = null;
  let playerListRenderedAt = 0;

  function renderPlayerListRows(players) {

    KBCDom.render(
      $("player-list"),
      (players || []).map(p =>
        KBCDom.el("div", { class: "player-row" }, [
          KBCDom.el("span", {}, [
            KBCDom.el("span", { class: p.connected ? "dot on" : "dot" }),
            " ",
            p.name
          ])
        ])
      )
    );
  }

  function renderPlayers(players, total) {

    const countEl = $("player-count");

    if (countEl) {

      countEl.textContent =
        total != null ? total : (players?.length || 0);
    }

    const now = Date.now();
    const sinceLast = now - playerListRenderedAt;

    if (sinceLast >= PLAYER_LIST_INTERVAL_MS) {

      /* Leading edge: rebuild immediately. */
      playerListRenderedAt = now;
      renderPlayerListRows(players);
      return;
    }

    /*
     * Trailing edge: coalesce everything in this window into one rebuild
     * with the latest data.
     */
    playerListPending = players;

    if (!playerListTimer) {

      playerListTimer = setTimeout(() => {
        playerListTimer = null;
        playerListRenderedAt = Date.now();
        renderPlayerListRows(playerListPending);
        playerListPending = null;
      }, PLAYER_LIST_INTERVAL_MS - sinceLast);
    }
  }

  /* =========================================================
     LEADERBOARD
  ========================================================= */

  function renderLB(rows) {

    KBCDom.render(
      $("leaderboard-body"),
      (rows || []).map(x =>
        KBCDom.el("div", { class: "leader-row" }, [
          KBCDom.el("b", { text: `#${Number(x.rank || 0)}` }),
          KBCDom.el("span", { text: x.name }),
          KBCDom.el("b", { text: money(x.prize) }),
          KBCDom.el("span", {
            text: `${(Number(x.time_ms || 0) / 1000).toFixed(2)} s`
          })
        ])
      )
    );
  }

  /* =========================================================
     FINAL RESULTS
  ========================================================= */

  function renderFinalResults(rows) {

    const panel = $("final-results");
    const body = $("final-results-body");

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
     FASTEST FINGER RESULTS
  ========================================================= */

  /*
   * Flatten an answer into display text. Only strings/numbers survive; DOM
   * nodes or any other object are skipped, because String()-ing a node is
   * what produced "[object HTMLBRElement]" in the results table.
   */
  function answerText(parts) {

    const flat = [];

    const walk = value => {
      if (value == null) return;
      if (typeof value === "string" || typeof value === "number") {
        flat.push(String(value));
      } else if (Array.isArray(value)) {
        value.forEach(walk);
      }
    };

    walk(parts);

    return flat.join(" → ");
  }

  function renderFastestResults(ff) {

    const body = $("fastest-results-body");
    if (!ff || !body) return;

    const options =
      (ff.prompt?.options || []).map((option, index) =>
        KBCDom.el("li", {}, [
          KBCDom.el("b", { text: `${index + 1}.` }),
          " ",
          answerText(option)
        ])
      );

    const results =
      ff.results?.length
        ? KBCDom.table(
            ["Rank", "Player", "Answer", "Result", "Time"],
            ff.results.map((x, i) => [
              `#${i + 1}`,
              x.name,
              answerText(x.answer),
              KBCDom.cell(
                x.correct ? "Correct" : "Wrong",
                { class: x.correct ? "answer-correct" : "answer-wrong" }
              ),
              `${(Number(x.time || 0) / 1000).toFixed(2)} s`
            ])
          )
        : KBCDom.el("p", { text: "No submissions yet." });

    KBCDom.render(body, [
      KBCDom.el("p", {
        class: "fastest-result-question",
        text: ff.prompt?.text || ""
      }),

      KBCDom.el("ol", { class: "fastest-option-list" }, options),

      KBCDom.el("p", { class: "fastest-correct-answer" }, [
        KBCDom.el("b", { text: "Correct answer:" }),
        " ",
        answerText(ff.prompt?.correct_order)
      ]),

      results
    ]);
  }

  /* =========================================================
     DELTA
  ========================================================= */

  function renderAnswerStats(data) {

    const panel = $("answer-stats");
    if (!panel) return;

    const questionLive = [
      "QUESTION_ACTIVE",
      "PAUSED",
      "ANSWER_LOCKED",
      "REVEAL"
    ].includes(data.status);

    if (!data.question || !questionLive) {
      panel.classList.add("hidden");
      KBCDom.clear(panel);
      return;
    }

    const distribution =
      data.answers?.distribution || {
        A: 0, B: 0, C: 0, D: 0
      };

    const submitted =
      Number(data.answers?.submitted || 0);

    const total =
      Number(data.players?.length || data.player_count || 0);

    panel.classList.remove("hidden");

    KBCDom.render(panel, [
      KBCDom.el("div", {
        class: "answer-summary",
        text: `Answered ${submitted} / ${total}`
      }),

      ..."ABCD".split("").map(letter =>
        KBCDom.el("div", { class: "stat" }, [
          KBCDom.el("b", { text: letter }),
          KBCDom.el("br"),
          String(Number(distribution[letter] || 0))
        ])
      )
    ]);
  }

  function updateOptionCounts(distribution) {

    const counts =
      $("options")?.querySelectorAll(".option-count") || [];

    "ABCD".split("").forEach(letter => {
      const node = counts[letter.charCodeAt(0) - 65];
      if (node) {
        node.textContent = String(Number(distribution?.[letter] || 0));
      }
    });
  }

  function updateFastestSubmissions(submissions) {

    const panel = $("fastest-panel");
    if (!panel) return;

    const line = Array.from(panel.querySelectorAll("p")).find(el =>
      el.textContent.indexOf("Submissions:") === 0
    );

    if (line) {
      line.textContent = `Submissions: ${Number(submissions || 0)}`;
    }
  }

  function renderDelta(
    delta
  ) {

    if (!delta) return;

    /*
     * A delta carries only counters. It must NOT call render(): with 200
     * players every single answer produces a delta, and a full render per
     * delta would rebuild the player list, leaderboard, options and timer
     * hundreds of times per question.
     */

    if (delta.answers) {
      if (lastState) {
        lastState.answers = delta.answers;
        renderAnswerStats(lastState);
      }
      updateOptionCounts(delta.answers.distribution);
    }

    if (
      delta.fastest &&
      delta.fastest.submissions !==
        undefined
    ) {

      if (lastState?.fastest) {
        lastState.fastest = {
          ...lastState.fastest,
          submissions: delta.fastest.submissions
        };
      }

      updateFastestSubmissions(delta.fastest.submissions);
    }
  }

  /* =========================================================
     MAIN HOST RENDER
  ========================================================= */

  function render(data) {

    if (!data) return;

    lastState = data;

    /*
     * Sound
     */
    if (
      previousStatus !==
      data.status
    ) {

      if (
        data.status ===
        "QUESTION_ACTIVE"
      ) {
        playCue("start");
      }

      if (
        data.status ===
        "REVEAL"
      ) {
        playCue("reveal");
      }

      if (
        data.status ===
          "GAME_OVER" ||
        data.status ===
          "FINAL"
      ) {
        playCue("end");
      }

      previousStatus =
        data.status;
    }

    /*
     * Basic state
     */
    $("stage-state").textContent =
      data.status;

    $("question-number").textContent =
      `${data.current_question || 0} / ${data.total_questions}`;

    renderPlayers(
      data.players,
      Number(data.player_count ?? data.players?.length ?? 0)
    );

    renderLB(
      data.leaderboard
    );

    renderFinalResults(
      data.final_results
    );

    /*
     * Question
     */
    if (data.question) {

      $("category").textContent =
        `${data.question.category} · ${data.question.difficulty}`;

      $("question-text").textContent =
        data.question.text;

      const distribution =
        data.answers?.distribution || {};

      const showCounts = [
        "QUESTION_ACTIVE",
        "PAUSED",
        "ANSWER_LOCKED",
        "REVEAL"
      ].includes(data.status);

      KBCDom.render(
        $("options"),
        Object.entries(
          data.question.options || {}
        )
        .map(([k, v]) =>
          KBCDom.el("div", { class: "option" }, [
            KBCDom.el("b", { text: `${k}.` }),
            " ",
            v,
            showCounts
              ? KBCDom.el("span", {
                  class: "option-count",
                  text: String(Number(distribution[k] || 0))
                })
              : null
          ])
        )
      );
    } else if ($("options")) {
      KBCDom.clear($("options"));
    }

    renderAnswerStats(data);

    /*
     * Reveal
     */
    if (
      data.question?.correct_option
    ) {

      const revealPanel = $("reveal");
      revealPanel.classList.remove("hidden");

      KBCDom.render(revealPanel, [
        KBCDom.el("b", {
          text: `CORRECT ANSWER: ${data.question.correct_option}`
        }),

        data.question.explanation
          ? KBCDom.el("span", { class: "muted" }, [
              KBCDom.el("br"),
              data.question.explanation
            ])
          : null
      ]);

    } else {

      $("reveal")
        .classList
        .add("hidden");
    }

    /* =====================================================
       FASTEST FINGER BUTTON STATE
       
       Backend should ideally send:
       data.fastest.can_start

       But this JS also has a fallback:
       !started && !locked
    ===================================================== */

    const fastest =
      data.fastest || {};

    const fastestStarted =
      Boolean(
        fastest.started
      );

    const fastestLocked =
      Boolean(
        fastest.locked
      );

    const canStartFastest =
    fastest.can_start !== undefined
        ? Boolean(fastest.can_start)
        : (
            (
                data.status === "LOBBY" ||
                data.status === "FASTEST_FINGER"
            ) &&
            !fastestStarted &&
            !fastestLocked
        );

    /*
     * Find buttons by data-action.
     *
     * No HTML ID change is required.
     */
    const startFastestButton =
      document.querySelector(
        '[data-action="start_fastest"]'
      );

    const finishFastestButton =
      document.querySelector(
        '[data-action="finish_fastest"]'
      );

    /*
     * START FASTEST
     *
     * Only possible once.
     */
    if (startFastestButton) {

      startFastestButton.disabled =
        !canStartFastest;

      startFastestButton.classList.toggle(
        "disabled",
        !canStartFastest
      );

      if (fastestStarted && fastestLocked) {

        startFastestButton.textContent =
          "Restart Fastest Finger";

      } else if (fastestStarted) {

        startFastestButton.textContent =
          "Fastest Finger Started";

      } else {

        startFastestButton.textContent =
          "Start Fastest Finger";
      }
    }

    const setActionEnabled = (action, enabled) => {
      const button = document.querySelector(`[data-action="${action}"]`);
      if (!button) return;
      button.disabled = !enabled;
      button.classList.toggle("disabled", !enabled);
    };

    const canStartQuestion =
      (
        data.status === "FASTEST_FINGER" &&
        fastestStarted &&
        fastestLocked &&
        data.current_question === 0
      ) ||
      (
        (data.status === "REVEAL" || data.status === "LEADERBOARD") &&
        data.current_question < data.total_questions
      );

    setActionEnabled("start_question", canStartQuestion);
    setActionEnabled("pause", data.status === "QUESTION_ACTIVE");
    setActionEnabled("resume", data.status === "PAUSED");
    setActionEnabled("lock", data.status === "QUESTION_ACTIVE");
    setActionEnabled("reveal", data.status === "ANSWER_LOCKED");
    setActionEnabled("next", data.status === "REVEAL");
    setActionEnabled("end", data.status !== "GAME_OVER" && data.status !== "FINAL");

    /*
     * FINISH FASTEST
     *
     * Enabled only after start and
     * before lock.
     */
    if (finishFastestButton) {

      const canFinishFastest =
    data.status === "FASTEST_FINGER" &&
    fastestStarted &&
    !fastestLocked;

      finishFastestButton.disabled =
        !canFinishFastest;

      finishFastestButton.classList.toggle(
        "disabled",
        !canFinishFastest
      );

      if (fastestLocked) {

        finishFastestButton.textContent =
          "Fastest Finger Finished";

      } else {

        finishFastestButton.textContent =
          "Finish Fastest Finger";
      }
    }

    /*
     * Fastest Finger display
     */
    if (
      fastestStarted &&
      !fastestLocked
    ) {

      $("fastest-panel")
        .classList
        .remove("hidden");

      KBCDom.render(
        $("fastest-panel"),
        [
          KBCDom.el("div", {
            class: "eyebrow",
            text: "FASTEST FINGER FIRST"
          }),

          KBCDom.el("h2", {
            text: fastest.prompt?.text || ""
          }),

          KBCDom.el(
            "div",
            { class: "fastest-options" },
            (fastest.prompt?.options || []).map((option, index) =>
              KBCDom.el("div", { class: "fastest-choice" }, [
                KBCDom.el("b", { text: `${index + 1}.` }),
                " ",
                answerText(option)
              ])
            )
          ),

          KBCDom.el("p", {
            text: `Submissions: ${Number(fastest.submissions || 0)}`
          })
        ]
      );

    } else {

      $("fastest-panel")
        .classList
        .add("hidden");
    }

    /*
     * Results
     */
    renderFastestResults(
      data.fastest
    );

    /*
     * Question timer
     */
    startTimer(data);
  }

  /* =========================================================
     TIMER
  ========================================================= */

  function startTimer(data) {

    clearInterval(timerHandle);

    const el = $("timer");
    if (!el) return;

    const status = data?.status;
    el.classList.toggle("paused", status === "PAUSED");

    if (status === "PAUSED") {
      const remaining = data.paused_remaining_seconds;
      el.textContent = remaining == null
        ? "PAUSED"
        : String(Math.max(0, Number(remaining)));
      return;
    }

    if (status !== "QUESTION_ACTIVE" || !data.deadline) {
      el.textContent = "—";
      return;
    }

    const tick = () => {
      el.textContent = Math.max(
        0,
        Math.ceil((new Date(data.deadline) - Date.now()) / 1000)
      );
    };

    tick();
    timerHandle = setInterval(tick, 200);
  }

  /* =========================================================
     BUTTON EVENTS
  ========================================================= */

  $("create-room").onclick =
    create;

  /*
   * Generic host action buttons.
   */
  document
    .querySelectorAll(
      "[data-action]"
    )
    .forEach(btn => {

      btn.onclick = () => {

        if (!roomCode) {

          show(
            "Create a room first."
          );

          return;
        }

        /*
         * Important:
         * Disabled button should NEVER send action.
         */
        if (btn.disabled) {
          return;
        }

        const action =
          btn.dataset.action;

        /*
         * Extra client-side protection
         * for Fastest Finger.
         */
        if (
          action ===
            "start_fastest"
        ) {

          /*
           * Immediately disable it.
           * Backend remains the final authority.
           */
          btn.disabled = true;

          btn.textContent =
            "Starting Fastest Finger...";
        }

        send(
          action,
          {
            sequence:
              action ===
              "start_question"
                ? null
                : undefined
          }
        );
      };
    });

  /* =========================================================
     SOUND
  ========================================================= */

  $("sound-toggle").onclick =
    () => {

      soundOn =
        !soundOn;

      if (soundOn) {

        audioContext =
          audioContext ||
          new (
            window.AudioContext ||
            window.webkitAudioContext
          )();

        playCue("click");
      }

      $("sound-toggle")
        .textContent =
        soundOn
          ? "🔊 Sound ON"
          : "🔇 Sound OFF";
    };

  /* =========================================================
     FULLSCREEN
  ========================================================= */

  $("fullscreen").onclick =
    () =>
      document.documentElement
        .requestFullscreen?.();

  /* =========================================================
     LEADERBOARD MODAL
  ========================================================= */

  $("leaderboard-open").onclick =
    () =>
      $("leaderboard-modal")
        .classList
        .remove("hidden");

  $("leaderboard-close").onclick =
    () =>
      $("leaderboard-modal")
        .classList
        .add("hidden");

  /* =========================================================
     FASTEST RESULTS MODAL
  ========================================================= */

  $("fastest-results-open").onclick =
    () =>
      $("fastest-results-modal")
        .classList
        .remove("hidden");

  $("fastest-results-close").onclick =
    () =>
      $("fastest-results-modal")
        .classList
        .add("hidden");

  /* =========================================================
     EXISTING ROOM
  ========================================================= */

  if (roomCode) {

    $("room-code").textContent =
      roomCode;

    connect();
  }

})();
