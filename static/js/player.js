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

        console.log("WS MESSAGE:", m);

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

  function applyAction(action, result) {
    if (!state) return;

    if (action === "answer") {

      state.my_answer =
        state.my_answer || {
          attempts: []
        };

      state.my_answer.attempts =
        result.attempts || [];

      state.my_answer.locked =
        Boolean(result.locked);

      if (result.locked) {
        state.my_answer.correct =
          result.correct;
      }

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

    /*
     * Additional protection:
     * If backend keeps the same question number but sends
     * a different question object, treat it as new question.
     */
    const previousQuestionId =
      state.question?.id ??
      state.question?.pk ??
      state.question?.text ??
      null;

    const incomingQuestionId =
      s.question?.id ??
      s.question?.pk ??
      s.question?.text ??
      null;

    const questionObjectChanged =
      previousQuestionId !== null &&
      incomingQuestionId !== null &&
      previousQuestionId !== incomingQuestionId;

    const isNewQuestion =
      questionChanged ||
      questionObjectChanged;

    /*
     * Fastest Finger local state should only survive
     * while the same Fastest Finger round is active.
     */
    const previousFastestPrompt =
      state.fastest?.prompt?.text || null;

    const incomingFastestPrompt =
      s.fastest?.prompt?.text || null;

    const fastestRoundChanged =
      previousFastestPrompt &&
      incomingFastestPrompt &&
      previousFastestPrompt !== incomingFastestPrompt;

    state = {
      ...state,
      ...s,

      /*
       * These are player-private values.
       * Do not overwrite them with public room state.
       */
      me: state.me,

      /*
       * QUESTION-SPECIFIC STATE
       *
       * New question => completely fresh answer state.
       */
      my_answer: isNewQuestion
        ? null
        : state.my_answer,

      removed_options: isNewQuestion
        ? []
        : state.removed_options,

      active_poll: isNewQuestion
        ? null
        : state.active_poll,

      expert: isNewQuestion
        ? null
        : state.expert,

      expert_incoming: isNewQuestion
        ? null
        : state.expert_incoming,

      /*
       * FASTEST FINGER
       */
      fastest: fastestRoundChanged
        ? {
            ...(s.fastest || {})
          }
        : {
            ...(state.fastest || {}),
            ...(s.fastest || {})
          }
    };

    /*
     * If the room moved out of QUESTION_ACTIVE,
     * old answer selection should not remain active.
     */
    if (s.status !== "QUESTION_ACTIVE") {
      if (
        s.status === "FASTEST_FINGER" ||
        s.status === "LOBBY"
      ) {
        state.my_answer = null;
        state.removed_options = [];
      }
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
     * This is especially important after reconnect.
     */
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

      const attempts =
        s.my_answer?.attempts || [];

      const locked =
        Boolean(s.my_answer?.locked);

      const revealed =
        Boolean(s.question.correct_option);

      const wrong =
        revealed &&
        s.my_answer?.selected &&
        s.my_answer?.correct === false;

      $("player-options").innerHTML =
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
                        s.my_answer?.correct
                          ? "selected-correct"
                          : (
                              wrong &&
                              s.my_answer?.selected === k
                                ? "selected-wrong"
                                : "selected"
                            )
                      )
                    : "selected"
                )
              : "";

          const removed =
            s.removed_options?.includes(k);

          return `
            <button
              class="option ${selectedClass} ${removed ? "removed" : ""}"
              data-option="${k}"
              ${disabled || removed ? "disabled" : ""}
            >
              <b>${k}.</b>
              ${escapeHtml(v)}
            </button>
          `;
        })
        .join("");

      /*
       * Option click
       */
      document
        .querySelectorAll("[data-option]")
        .forEach(button => {

          button.onclick = () => {

            if (
              button.disabled ||
              state.status !== "QUESTION_ACTIVE"
            ) {
              return;
            }

            /*
             * Immediately update UI.
             */
            document
              .querySelectorAll("[data-option]")
              .forEach(o =>
                o.classList.remove("selected")
              );

            button.classList.add("selected");

            /*
             * Send answer.
             */
            send("answer", {
              option: button.dataset.option
            });
          };
        });

      renderLifelines(
        s.me?.lifelines || {}
      );

      /*
       * Reveal result
       */
      if (s.question.correct_option) {

        $("result").classList.remove("hidden");

        const ok =
          s.my_answer?.selected ===
          s.question.correct_option;

        $("result").innerHTML =
          `
            <b>
              ${ok ? "✅ CORRECT" : "❌ WRONG"}
            </b>
            —
            Correct answer:
            ${s.question.correct_option}
          `;

      } else {

        $("result").classList.add("hidden");
      }

    } else {

      /*
       * No question currently.
       */
      if ($("player-options")) {
        $("player-options").innerHTML = "";
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

    startTimer(
      s.status === "QUESTION_ACTIVE"
        ? s.deadline
        : null
    );

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

    if (!panel) return;

    if (!rows?.length) {
      panel.classList.add("hidden");
      return;
    }

    panel.classList.remove("hidden");

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
              <td>${escapeHtml(x.name)}</td>
              <td>${money(x.score)}</td>

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
                ${(Number(x.time_ms || 0) / 1000).toFixed(2)}
                s
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
      `;
  }

  /* =========================================================
     AUDIENCE POLL
  ========================================================= */

  function renderPoll(s) {

    const p = s.active_poll;

    if (!p) {
      $("poll-box").classList.add("hidden");
      return;
    }

    $("poll-box").classList.remove("hidden");

    if (p.requester && p.completed) {

      $("poll-box").innerHTML =
        `
        <b>Audience Poll Result</b>

        <div class="stats">
          ${
            "ABCD"
              .split("")
              .map(x => `
                <div class="stat">
                  <b>${x}</b>
                  <br>
                  ${p.percentages?.[x] || 0}%
                </div>
              `)
              .join("")
          }
        </div>
        `;

    } else {

      $("poll-box").innerHTML =
        p.can_vote
          ? `
            <b>Audience Poll</b>

            <p>
              ${escapeHtml(
                p.requester_name ||
                "Another player"
              )}
              needs your vote.
            </p>

            <div class="options-grid">
              ${
                "ABCD"
                  .split("")
                  .map(x => `
                    <button
                      class="btn"
                      onclick="window.votePoll(${p.id}, '${x}')"
                    >
                      ${x}
                    </button>
                  `)
                  .join("")
              }
            </div>
          `
          : `
            <b>Audience Poll active</b>
            <p>Waiting for other players…</p>
          `;
    }
  }

  window.votePoll = (id, option) =>
    send("audience_vote", {
      poll_id: id,
      option
    });

  /* =========================================================
     EXPERT
  ========================================================= */

  function renderExpert(s) {

    if (
      s.expert &&
      !s.expert.answered
    ) {

      $("expert-box").classList.remove("hidden");

      $("expert-box").innerHTML =
        `
        <b>Expert selected:</b>
        ${escapeHtml(s.expert.expert_name)}
        <br>
        Waiting for their answer…
        `;

    }

    else if (s.expert?.answer) {

      $("expert-box").classList.remove("hidden");

      $("expert-box").innerHTML =
        `
        <b>
          ${escapeHtml(
            s.expert.expert_name
          )}
          answered:
        </b>
        ${s.expert.answer}
        `;

    }

    else if (
      s.expert_incoming &&
      !s.expert_incoming.answered
    ) {

      $("expert-box").classList.remove("hidden");

      $("expert-box").innerHTML =
        `
        <b>You are the Expert</b>

        <p>
          ${escapeHtml(
            s.expert_incoming.question.text
          )}
        </p>

        <div class="options-grid">
          ${
            "ABCD"
              .split("")
              .map(x => `
                <button
                  class="btn"
                  onclick="window.expert(${s.expert_incoming.request_id}, '${x}')"
                >
                  ${x}
                </button>
              `)
              .join("")
          }
        </div>
        `;

    }

    else {

      $("expert-box").classList.add("hidden");
    }
  }

  window.expert = (id, option) =>
    send("expert_answer", {
      request_id: id,
      option
    });

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

    $("ff-items").innerHTML =
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

          return `
            <button
              class="fastest-choice ${resultClass}"
              data-fastest-option="${index}"
              ${
                ff.locked ||
                ff.submitted
                  ? "disabled"
                  : ""
              }
            >
              <b>${index + 1}.</b>
              ${option.map(escapeHtml).join(" → ")}
            </button>
          `;
        })
        .join("");

    $("ff-submit").disabled = true;

    $("ff-submit").textContent =
      ff.locked || ff.submitted
        ? "Answer Locked"
        : "Choose an Order Above";

    /*
     * Fastest Finger option click
     */
    $("ff-items")
      .querySelectorAll(
        "[data-fastest-option]"
      )
      .forEach(button => {

        button.onclick = () => {

          if (
            ff.locked ||
            ff.submitted
          ) {
            return;
          }

          const selectedIndex =
            Number(
              button.dataset.fastestOption
            );

          const answer =
            ff.prompt.options[
              selectedIndex
            ];

          /*
           * Visual selection
           */
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
        };
      });
  }

  /* =========================================================
     TIMER
  ========================================================= */

  function startTimer(deadline) {

    clearInterval(timerHandle);

    if (!deadline) {

      $("player-timer").textContent =
        "—";

      return;
    }

    timerHandle =
      setInterval(() => {

        $("player-timer").textContent =
          Math.max(
            0,
            Math.ceil(
              (
                new Date(deadline) -
                Date.now()
              ) / 1000
            )
          );

      }, 200);
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















// (function(){
//   const code=window.ROOM_CODE;const session=localStorage.getItem("player_session")||"";
//   let socket=null,reconnectTimer=null,fallbackTimer=null,timerHandle=null,state=null;
//   const $=id=>document.getElementById(id);const money=n=>"₹"+Number(n||0).toLocaleString("en-IN");
//   const wsUrl=()=>`${location.protocol==="https:"?"wss":"ws"}://${location.host}/ws/rooms/${code}/`;
//   function show(msg){$("player-message").textContent=msg;$("player-message").classList.remove("hidden");setTimeout(()=>$("player-message").classList.add("hidden"),3200)}
//   function escapeHtml(s){return String(s??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[m]))}
//   function connect(){
//     if(!code||!session)return show("Player session missing. Please join again.");
//     clearTimeout(reconnectTimer);try{socket?.close()}catch(_){ }
//     socket=new WebSocket(wsUrl());
//     socket.onopen=()=>{socket.send(JSON.stringify({type:"auth",role:"player",token:session}));clearInterval(fallbackTimer)};
//     socket.onmessage=e=>{try{const m=JSON.parse(e.data);if(m.type==="authenticated"||m.type==="player.private")applyState(m.state);else if(m.type==="room.state")applyPublic(m.state);else if(m.type==="action.ok")applyAction(m.action,m.result);else if(m.type==="error"||m.type==="auth_error")show(m.error)}catch(_){}};
//     socket.onclose=()=>{fallbackFetch();reconnectTimer=setTimeout(connect,2000)};
//     socket.onerror=()=>{try{socket.close()}catch(_){}};
//   }
//   function send(action,body={}){if(!socket||socket.readyState!==WebSocket.OPEN){show("Reconnecting…");return}socket.send(JSON.stringify({type:"action",action,...body}))}
//   async function fallbackFetch(){try{const r=await fetch(`/api/rooms/${code}/state/`,{headers:{"X-Player-Session":session}}),d=await r.json();if(d.ok)applyState(d.state)}catch(_){ }clearInterval(fallbackTimer);fallbackTimer=setInterval(fallbackFetch,5000)}

//   function applyAction(action,result){
//     if(!state)return;
//     if(action==="answer"){
//       state.my_answer=state.my_answer||{attempts:[]};
//       state.my_answer.attempts=result.attempts||state.my_answer.attempts;
//       state.my_answer.locked=Boolean(result.locked);
//       if(result.locked)state.my_answer.correct=result.correct;
//       render(state);
//     }else if(action==="fastest_submit"){
//       state.fastest=state.fastest||{};state.fastest.submitted=true;state.fastest.submitted_correct=result.correct;render(state);
//     }else if(action==="lifeline"){
//       state.me=state.me||{lifelines:{}};state.me.lifelines=state.me.lifelines||{};state.me.lifelines[result.lifeline]=false;
//       if(result.payload?.removed)state.removed_options=result.payload.removed;
//       if(result.payload?.question)state.question=result.payload.question;
//       render(state);
//     }else if(action==="audience_vote"){
//       if(state.active_poll)state.active_poll.can_vote=false;render(state);
//     }else if(action==="expert_answer"){
//       if(state.expert_incoming)state.expert_incoming.answered=true;render(state);
//     }
//   }
//   function applyPublic(s){if(!state)state=s;else state={...state,...s,me:state.me,my_answer:state.my_answer,removed_options:state.removed_options,active_poll:state.active_poll,expert:state.expert,expert_incoming:state.expert_incoming,fastest:{...(state.fastest||{}),...(s.fastest||{})}};render(state)}
//   function applyState(s){state=s;render(s)}
//   function render(s){
//     $("my-name").textContent=s.me?.name||"";$("my-prize").textContent=money(s.me?.prize);$("q-num").textContent=`${s.current_question||0} / ${s.total_questions}`;
//     if(s.question){$("category").textContent=`${s.question.category} · ${s.question.difficulty}`;$("question").textContent=s.question.text;const attempts=s.my_answer?.attempts||[],locked=s.my_answer?.locked,revealed=Boolean(s.question.correct_option),wrong=revealed&&s.my_answer?.selected&&s.my_answer?.correct===false;
//       $("player-options").innerHTML=Object.entries(s.question.options).map(([k,v])=>{const disabled=locked||attempts.includes(k)||s.status!=="QUESTION_ACTIVE";const selectedClass=attempts.includes(k)?(revealed?(s.my_answer.correct?"selected-correct":(wrong&&s.my_answer.selected===k?"selected-wrong":"selected")):"selected"):"";const removed=s.removed_options?.includes(k);return `<button class="option ${selectedClass} ${removed?"removed":""}" data-option="${k}" ${disabled||removed?"disabled":""}><b>${k}.</b> ${escapeHtml(v)}</button>`}).join("");
//       document.querySelectorAll("[data-option]").forEach(b=>b.onclick=()=>{document.querySelectorAll("[data-option]").forEach(o=>o.classList.remove("selected"));b.classList.add("selected");send("answer",{option:b.dataset.option})});
//       renderLifelines(s.me?.lifelines||{});
//       if(s.question.correct_option){$("result").classList.remove("hidden");const ok=s.my_answer?.selected===s.question.correct_option;$("result").innerHTML=`<b>${ok?"✅ CORRECT":"❌ WRONG"}</b> — Correct answer: ${s.question.correct_option}`}else $("result").classList.add("hidden");
//     }
//     renderFinalResults(s.final_results);renderPoll(s);renderExpert(s);startTimer(s.status==="QUESTION_ACTIVE"?s.deadline:null);renderFastest(s.fastest,s.status);
//   }
//   function renderLifelines(l){document.querySelectorAll(".life").forEach(b=>b.classList.toggle("used",l[b.dataset.life]===false))}
//   function renderFinalResults(rows){const panel=$("final-results");if(!rows?.length){panel.classList.add("hidden");return}panel.classList.remove("hidden");$("final-results-body").innerHTML=`<table class="results-table"><thead><tr><th>Player</th><th>Score</th><th>Correct Questions</th><th>Wrong Questions</th><th>Total Time</th></tr></thead><tbody>${rows.map(x=>`<tr><td>${escapeHtml(x.name)}</td><td>${money(x.score)}</td><td>${x.correct.length?x.correct.join(", "):"None"}</td><td>${x.wrong.length?x.wrong.join(", "):"None"}</td><td>${(x.time_ms/1000).toFixed(2)} s</td></tr>`).join("")}</tbody></table>`}
//   function renderPoll(s){const p=s.active_poll;if(!p){$("poll-box").classList.add("hidden");return}$("poll-box").classList.remove("hidden");if(p.requester&&p.completed){$("poll-box").innerHTML=`<b>Audience Poll Result</b><div class="stats">${"ABCD".split("").map(x=>`<div class="stat"><b>${x}</b><br>${p.percentages?.[x]||0}%</div>`).join("")}</div>`}else{$("poll-box").innerHTML=p.can_vote?`<b>Audience Poll</b><p>${escapeHtml(p.requester_name||"Another player")} needs your vote.</p><div class="options-grid">${"ABCD".split("").map(x=>`<button class="btn" onclick="window.votePoll(${p.id},'${x}')">${x}</button>`).join("")}</div>`:`<b>Audience Poll active</b><p>Waiting for other players…</p>`}}
//   window.votePoll=(id,option)=>send("audience_vote",{poll_id:id,option});
//   function renderExpert(s){if(s.expert&&!s.expert.answered){$("expert-box").classList.remove("hidden");$("expert-box").innerHTML=`<b>Expert selected:</b> ${escapeHtml(s.expert.expert_name)}<br>Waiting for their answer…`}else if(s.expert?.answer){$("expert-box").classList.remove("hidden");$("expert-box").innerHTML=`<b>${escapeHtml(s.expert.expert_name)} answered:</b> ${s.expert.answer}`}else if(s.expert_incoming&&!s.expert_incoming.answered){$("expert-box").classList.remove("hidden");$("expert-box").innerHTML=`<b>You are the Expert</b><p>${escapeHtml(s.expert_incoming.question.text)}</p><div class="options-grid">${"ABCD".split("").map(x=>`<button class="btn" onclick="window.expert(${s.expert_incoming.request_id},'${x}')">${x}</button>`).join("")}</div>`}else $("expert-box").classList.add("hidden")}
//   window.expert=(id,option)=>send("expert_answer",{request_id:id,option});
//   function renderFastest(ff,status){const visible=ff?.started;$("fastest").classList.toggle("hidden",!visible);if(!visible)return;$("ff-text").textContent=ff.prompt.text;const submittedAnswer=JSON.stringify(ff.submitted_answer||[]);$("ff-items").innerHTML=(ff.prompt.options||[]).map((option,index)=>{const selected=ff.submitted&&JSON.stringify(option)===submittedAnswer;const resultClass=selected?(ff.submitted_correct?"submitted":"submitted-wrong"):"";return `<button class="fastest-choice ${resultClass}" data-fastest-option="${index}" ${ff.locked||ff.submitted?"disabled":""}><b>${index+1}.</b> ${option.map(escapeHtml).join(" → ")}</button>`}).join("");$("ff-submit").disabled=true;$("ff-submit").textContent=ff.locked||ff.submitted?"Answer Locked":"Choose an Order Above";$("ff-items").querySelectorAll("[data-fastest-option]").forEach(button=>button.onclick=()=>{if(ff.locked||ff.submitted)return;send("fastest_submit",{answer:ff.prompt.options[Number(button.dataset.fastestOption)]})})}
//   function startTimer(deadline){clearInterval(timerHandle);if(!deadline){$("player-timer").textContent="—";return}timerHandle=setInterval(()=>{$("player-timer").textContent=Math.max(0,Math.ceil((new Date(deadline)-Date.now())/1000))},200)}
//   document.querySelectorAll(".life").forEach(b=>b.onclick=()=>{if(!b.classList.contains("used"))send("lifeline",{lifeline:b.dataset.life})});
//   connect();
// })();
