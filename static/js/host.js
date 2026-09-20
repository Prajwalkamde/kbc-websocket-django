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

  function escapeHtml(s) {

    return String(s ?? "").replace(
      /[&<>"']/g,
      m => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
      }[m])
    );
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

        console.log(
          "HOST WS MESSAGE:",
          m
        );

        if (
          m.type === "host.state" ||
          m.type === "authenticated"
        ) {

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

          show(m.error);
        }
        else if (
    m.type === "error" ||
    m.type === "auth_error"
) {
    actionInProgress = false;

    const btn = document.querySelector(
        '[data-action="start_fastest"]'
    );

    if (btn) {
        btn.disabled = false;
        btn.textContent = "Start Fastest Finger";
    }

    show(m.error);
}

{
    actionInProgress = false;
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
     PLAYERS
  ========================================================= */

  function renderPlayers(
    players
  ) {

    $("player-count").textContent =
      players?.length || 0;

    $("player-list").innerHTML =
      (players || [])
        .map(p => `
          <div class="player-row">
            <span>
              <span
                class="dot ${
                  p.connected
                    ? "on"
                    : ""
                }"
              ></span>

              ${escapeHtml(
                p.name
              )}
            </span>
          </div>
        `)
        .join("");
  }

  /* =========================================================
     LEADERBOARD
  ========================================================= */

  function renderLB(rows) {

    $("leaderboard-body").innerHTML =
      (rows || [])
        .map(x => `
          <div class="leader-row">

            <b>#${x.rank}</b>

            <span>
              ${escapeHtml(
                x.name
              )}
            </span>

            <b>
              ${money(x.prize)}
            </b>

            <span>
              ${
                (
                  Number(
                    x.time_ms || 0
                  ) / 1000
                ).toFixed(2)
              }
              s
            </span>

          </div>
        `)
        .join("");
  }

  /* =========================================================
     FINAL RESULTS
  ========================================================= */

  function renderFinalResults(
    rows
  ) {

    const panel =
      $("final-results");

    if (!panel) return;

    if (!rows?.length) {

      panel.classList.add(
        "hidden"
      );

      return;
    }

    panel.classList.remove(
      "hidden"
    );

    $("final-results-body").innerHTML =
      `
      <table class="results-table">

        <thead>
          <tr>
            <th>Player</th>
            <th>Score</th>
            <th>Correct Questions</th>
            <th>Wrong Questions</th>
            <th>Total Time</th>
          </tr>
        </thead>

        <tbody>

          ${rows.map(x => `
            <tr>

              <td>
                ${escapeHtml(
                  x.name
                )}
              </td>

              <td>
                ${money(
                  x.score
                )}
              </td>

              <td>
                ${
                  x.correct?.length
                    ? x.correct.join(", ")
                    : "None"
                }
              </td>

              <td>
                ${
                  x.wrong?.length
                    ? x.wrong.join(", ")
                    : "None"
                }
              </td>

              <td>
                ${
                  (
                    Number(
                      x.time_ms || 0
                    ) / 1000
                  ).toFixed(2)
                }
                s
              </td>

            </tr>
          `).join("")}

        </tbody>

      </table>
      `;
  }

  /* =========================================================
     FASTEST FINGER RESULTS
  ========================================================= */

  function renderFastestResults(
    ff
  ) {

    if (!ff) return;

    const options =
      ff.prompt?.options
        ?.map(
          (o, i) => `
            <li>
              <b>${i + 1}.</b>
              ${o.map(
                escapeHtml
              ).join(" → ")}
            </li>
          `
        )
        .join("") || "";

    const results =
      ff.results?.length
        ? `
          <table class="results-table">

            <thead>
              <tr>
                <th>Rank</th>
                <th>Player</th>
                <th>Answer</th>
                <th>Time</th>
              </tr>
            </thead>

            <tbody>

              ${ff.results.map(
                (x, i) => `
                  <tr>

                    <td>
                      #${i + 1}
                    </td>

                    <td>
                      ${escapeHtml(
                        x.name
                      )}
                    </td>

                    <td
                      class="${
                        x.correct
                          ? "answer-correct"
                          : "answer-wrong"
                      }"
                    >

                      ${
                        x.answer
                          .map(
                            escapeHtml
                          )
                          .join(
                            " → "
                          )
                      }

                      <br>

                      <small>
                        ${
                          x.correct
                            ? "Correct"
                            : "Wrong"
                        }
                      </small>

                    </td>

                    <td>
                      ${
                        (
                          Number(
                            x.time || 0
                          ) / 1000
                        ).toFixed(2)
                      }
                      s
                    </td>

                  </tr>
                `
              ).join("")}

            </tbody>

          </table>
        `
        : "<p>No submissions yet.</p>";

    $("fastest-results-body").innerHTML =
      `
      <p class="fastest-result-question">
        ${escapeHtml(
          ff.prompt?.text || ""
        )}
      </p>

      <ol class="fastest-option-list">
        ${options}
      </ol>

      <p class="fastest-correct-answer">

        <b>
          Correct answer:
        </b>

        ${
          ff.prompt?.correct_order
            ?.map(
              escapeHtml
            )
            .join(" → ") || ""
        }

      </p>

      ${results}
      `;
  }

  /* =========================================================
     DELTA
  ========================================================= */

  function renderDelta(
    delta
  ) {

    if (!delta) return;

    if (delta.answers) {

      $("answer-stats")
        .classList
        .remove("hidden");

      $("answer-stats").innerHTML =
        Object.entries(
          delta.answers.distribution
        )
        .map(
          ([k, v]) => `
            <div class="stat">
              <b>${k}</b>
              <br>
              ${v}
            </div>
          `
        )
        .join("");
    }

    if (
      delta.fastest &&
      delta.fastest.submissions !==
        undefined
    ) {

      const current =
        $("fastest-panel")
          ?.querySelector("p");

      if (current) {

        current.textContent =
          `Submissions: ${delta.fastest.submissions}`;
      }
    }
  }

  /* =========================================================
     MAIN HOST RENDER
  ========================================================= */

  function render(data) {

    if (!data) return;

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
      data.players
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

      $("options").innerHTML =
        Object.entries(
          data.question.options || {}
        )
        .map(
          ([k, v]) => `
            <div class="option">
              <b>${k}.</b>
              ${escapeHtml(v)}
            </div>
          `
        )
        .join("");
    }

    /*
     * Answer statistics
     */
    if (data.answers) {

      $("answer-stats")
        .classList
        .remove("hidden");

      $("answer-stats").innerHTML =
        Object.entries(
          data.answers.distribution
        )
        .map(
          ([k, v]) => `
            <div class="stat">
              <b>${k}</b>
              <br>
              ${v}
            </div>
          `
        )
        .join("");
    }

    /*
     * Reveal
     */
    if (
      data.question?.correct_option
    ) {

      $("reveal")
        .classList
        .remove("hidden");

      $("reveal").innerHTML =
        `
        <b>
          CORRECT ANSWER:
          ${data.question.correct_option}
        </b>

        ${
          data.question.explanation
            ? `
              <br>
              <span class="muted">
                ${escapeHtml(
                  data.question.explanation
                )}
              </span>
            `
            : ""
        }
        `;

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

      const options =
        fastest.prompt?.options
          ?.map(
            (o, i) => `
              <div class="fastest-choice">

                <b>
                  ${i + 1}.
                </b>

                ${o.map(
                  escapeHtml
                ).join(" → ")}

              </div>
            `
          )
          .join("") || "";

      $("fastest-panel").innerHTML =
        `
        <div class="eyebrow">
          FASTEST FINGER FIRST
        </div>

        <h2>
          ${escapeHtml(
            fastest.prompt?.text || ""
          )}
        </h2>

        <div class="fastest-options">
          ${options}
        </div>

        <p>
          Submissions:
          ${fastest.submissions || 0}
        </p>
        `;

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
    startTimer(
      data.status ===
        "QUESTION_ACTIVE"
        ? data.deadline
        : null
    );
  }

  /* =========================================================
     TIMER
  ========================================================= */

  function startTimer(
    deadline
  ) {

    clearInterval(
      timerHandle
    );

    if (!deadline) {

      $("timer").textContent =
        "—";

      return;
    }

    timerHandle =
      setInterval(
        () => {

          $("timer").textContent =
            Math.max(
              0,
              Math.ceil(
                (
                  new Date(
                    deadline
                  ) -
                  Date.now()
                ) / 1000
              )
            );

        },
        200
      );
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























// (function () {
//   const codeFromPath = location.pathname.split("/").filter(Boolean).pop();
//   let roomCode = codeFromPath && codeFromPath !== "host" ? codeFromPath.toUpperCase() : "";
//   let hostSession = localStorage.getItem("host_session") || "";
//   let socket = null, reconnectTimer = null, fallbackTimer = null, timerHandle = null;
//   let soundOn = true, audioContext = null, previousStatus = "";
//   const $ = id => document.getElementById(id);
//   const money = n => "₹" + Number(n || 0).toLocaleString("en-IN");
//   const wsUrl = () => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/rooms/${roomCode}/`;

//   function playTone(frequency, duration = .12, delay = 0) {
//     if (!soundOn) return; const AC = window.AudioContext || window.webkitAudioContext; if (!AC) return;
//     audioContext = audioContext || new AC(); const start = audioContext.currentTime + delay;
//     const oscillator = audioContext.createOscillator(), gain = audioContext.createGain();
//     oscillator.type = "sine"; oscillator.frequency.value = frequency; gain.gain.setValueAtTime(.0001, start);
//     gain.gain.exponentialRampToValueAtTime(.055, start + .015); gain.gain.exponentialRampToValueAtTime(.0001, start + duration);
//     oscillator.connect(gain).connect(audioContext.destination); oscillator.start(start); oscillator.stop(start + duration + .02);
//   }
//   function playCue(name) { if (name === "start") { playTone(392, .14); playTone(523, .18, .12) } if (name === "reveal") { playTone(523, .12); playTone(659, .18, .1) } if (name === "end") { playTone(659, .14); playTone(523, .14, .12); playTone(392, .22, .24) } if (name === "click") playTone(260, .06) }
//   function show(msg) { $("host-message").textContent = msg; $("host-message").classList.remove("hidden"); setTimeout(() => $("host-message").classList.add("hidden"), 3500) }
//   function escapeHtml(s) { return String(s ?? "").replace(/[&<>"']/g, m => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[m])) }

//   async function create() {
//     try {
//       const r = await fetch("/api/rooms/create/", { method: "POST", headers: { "X-CSRFToken": window.CSRF_TOKEN } }), d = await r.json();
//       if (!d.ok) throw new Error(d.error); roomCode = d.room_code; hostSession = d.host_session; localStorage.setItem("host_session", hostSession);
//       $("room-code").textContent = roomCode; history.replaceState({}, "", `/host/${roomCode}/`); connect();
//     } catch (e) { show(e.message) }
//   }
//   function connect() {
//     if (!roomCode || !hostSession) return;
//     clearTimeout(reconnectTimer); try { socket?.close() } catch (_) { }
//     socket = new WebSocket(wsUrl());
//     socket.onopen = () => { socket.send(JSON.stringify({ type: "auth", role: "host", token: hostSession })); clearInterval(fallbackTimer) };
//     socket.onmessage = e => { try { const m = JSON.parse(e.data); if (m.type === "host.state" || m.type === "authenticated") render(m.state); if (m.type === "host.delta") renderDelta(m.state); if (m.type === "error" || m.type === "auth_error") show(m.error) } catch (_) { } };
//     socket.onclose = () => { fallbackFetch(); reconnectTimer = setTimeout(connect, 2000) };
//     socket.onerror = () => { try { socket.close() } catch (_) { } };
//   }
//   function send(action, body = {}) {
//     if (!socket || socket.readyState !== WebSocket.OPEN) { show("Reconnecting…"); return }
//     socket.send(JSON.stringify({ type: "action", action, ...body }));
//   }
//   async function fallbackFetch() {
//     if (!roomCode) return;
//     try { const r = await fetch(`/api/rooms/${roomCode}/state/`, { headers: { "X-Host-Session": hostSession } }); const d = await r.json(); if (d.ok) render(d.state) } catch (_) { }
//     clearInterval(fallbackTimer); fallbackTimer = setInterval(fallbackFetch, 5000);
//   }
//   function renderPlayers(players) { $("player-count").textContent = players?.length || 0; $("player-list").innerHTML = (players || []).map(p => `<div class="player-row"><span><span class="dot ${p.connected ? "on" : ""}"></span> ${escapeHtml(p.name)}</span></div>`).join("") }
//   function renderLB(rows) { $("leaderboard-body").innerHTML = (rows || []).map(x => `<div class="leader-row"><b>#${x.rank}</b><span>${escapeHtml(x.name)}</span><b>${money(x.prize)}</b><span>${(x.time_ms / 1000).toFixed(2)} s</span></div>`).join("") }
//   function renderFinalResults(rows) { const panel = $("final-results"); if (!rows?.length) { panel.classList.add("hidden"); return } panel.classList.remove("hidden"); $("final-results-body").innerHTML = `<table class="results-table"><thead><tr><th>Player</th><th>Score</th><th>Correct Questions</th><th>Wrong Questions</th><th>Total Time</th></tr></thead><tbody>${rows.map(x => `<tr><td>${escapeHtml(x.name)}</td><td>${money(x.score)}</td><td>${x.correct.length ? x.correct.join(", ") : "None"}</td><td>${x.wrong.length ? x.wrong.join(", ") : "None"}</td><td>${(x.time_ms / 1000).toFixed(2)} s</td></tr>`).join("")}</tbody></table>` }
//   function renderFastestResults(ff) { if (!ff) return; const options = ff.prompt.options?.map((o, i) => `<li><b>${i + 1}.</b> ${o.map(escapeHtml).join(" → ")}</li>`).join("") || ""; const results = ff.results?.length ? `<table class="results-table"><thead><tr><th>Rank</th><th>Player</th><th>Answer</th><th>Time</th></tr></thead><tbody>${ff.results.map((x, i) => `<tr><td>#${i + 1}</td><td>${escapeHtml(x.name)}</td><td class="${x.correct ? "answer-correct" : "answer-wrong"}">${x.answer.map(escapeHtml).join(" → ")}<br><small>${x.correct ? "Correct" : "Wrong"}</small></td><td>${(x.time / 1000).toFixed(2)} s</td></tr>`).join("")}</tbody></table>` : "<p>No submissions yet.</p>"; $("fastest-results-body").innerHTML = `<p class="fastest-result-question">${escapeHtml(ff.prompt.text)}</p><ol class="fastest-option-list">${options}</ol><p class="fastest-correct-answer"><b>Correct answer:</b> ${ff.prompt.correct_order.map(escapeHtml).join(" → ")}</p>${results}` }
//   function renderDelta(delta) {
//     if (!delta) return;
//     if (delta.answers) {
//       $("answer-stats").classList.remove("hidden");
//       $("answer-stats").innerHTML = Object.entries(delta.answers.distribution).map(([k, v]) => `<div class="stat"><b>${k}</b><br>${v}</div>`).join("");
//     }
//     if (delta.fastest && delta.fastest.submissions !== undefined) {
//       const current = $("fastest-panel").querySelector("p");
//       if (current) current.textContent = `Submissions: ${delta.fastest.submissions}`;
//     }
//   }
//   function render(data) {
//     if (!data) return;
//     if (previousStatus !== data.status) { if (data.status === "QUESTION_ACTIVE") playCue("start"); if (data.status === "REVEAL") playCue("reveal"); if (data.status === "GAME_OVER" || data.status === "FINAL") playCue("end"); previousStatus = data.status }
//     $("stage-state").textContent = data.status; $("question-number").textContent = `${data.current_question || 0} / ${data.total_questions}`;
//     renderPlayers(data.players); renderLB(data.leaderboard); renderFinalResults(data.final_results);
//     if (data.question) { $("category").textContent = `${data.question.category} · ${data.question.difficulty}`; $("question-text").textContent = data.question.text; $("options").innerHTML = Object.entries(data.question.options).map(([k, v]) => `<div class="option"><b>${k}.</b> ${escapeHtml(v)}</div>`).join("") }
//     if (data.answers) { $("answer-stats").classList.remove("hidden"); $("answer-stats").innerHTML = Object.entries(data.answers.distribution).map(([k, v]) => `<div class="stat"><b>${k}</b><br>${v}</div>`).join("") }
//     if (data.question?.correct_option) { $("reveal").classList.remove("hidden"); $("reveal").innerHTML = `<b>CORRECT ANSWER: ${data.question.correct_option}</b>${data.question.explanation ? `<br><span class="muted">${escapeHtml(data.question.explanation)}</span>` : ""}` } else $("reveal").classList.add("hidden");
//     if (data.fastest?.started && !data.fastest.locked) { $("fastest-panel").classList.remove("hidden"); const ff = data.fastest; const options = ff.prompt.options?.map((o, i) => `<div class="fastest-choice"><b>${i + 1}.</b> ${o.map(escapeHtml).join(" → ")}</div>`).join("") || ""; $("fastest-panel").innerHTML = `<div class="eyebrow">FASTEST FINGER FIRST</div><h2>${escapeHtml(ff.prompt.text)}</h2><div class="fastest-options">${options}</div><p>Submissions: ${ff.submissions}</p>` } else $("fastest-panel").classList.add("hidden");
//     renderFastestResults(data.fastest); startTimer(data.status === "QUESTION_ACTIVE" ? data.deadline : null);
//   }
//   function startTimer(deadline) { clearInterval(timerHandle); if (!deadline) { $("timer").textContent = "—"; return } timerHandle = setInterval(() => { $("timer").textContent = Math.max(0, Math.ceil((new Date(deadline) - Date.now()) / 1000)) }, 200) }

//   $("create-room").onclick = create;
//   document.querySelectorAll("[data-action]").forEach(btn => btn.onclick = () => { if (!roomCode) return show("Create a room first."); send(btn.dataset.action, { sequence: btn.dataset.action === "start_question" ? null : undefined }) });
//   $("sound-toggle").onclick = () => { soundOn = !soundOn; if (soundOn) { audioContext = audioContext || new (window.AudioContext || window.webkitAudioContext)(); playCue("click") } $("sound-toggle").textContent = soundOn ? "🔊 Sound ON" : "🔇 Sound OFF" };
//   $("fullscreen").onclick = () => document.documentElement.requestFullscreen?.();
//   $("leaderboard-open").onclick = () => $("leaderboard-modal").classList.remove("hidden"); $("leaderboard-close").onclick = () => $("leaderboard-modal").classList.add("hidden");
//   $("fastest-results-open").onclick = () => $("fastest-results-modal").classList.remove("hidden"); $("fastest-results-close").onclick = () => $("fastest-results-modal").classList.add("hidden");
//   if (roomCode) { $("room-code").textContent = roomCode; connect() }
// })();
