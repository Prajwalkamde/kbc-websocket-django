(function(){
  const form=document.getElementById("join-form"), error=document.getElementById("join-error");
  form.addEventListener("submit", async e=>{
    e.preventDefault(); error.classList.add("hidden");
    const name=document.getElementById("name").value.trim();
    const code=document.getElementById("code").value.trim().toUpperCase();
    try{
      const r=await fetch("/api/rooms/join/",{method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":window.CSRF_TOKEN},body:JSON.stringify({display_name:name,room_code:code})});
      const d=await r.json(); if(!d.ok) throw new Error(d.error);
      localStorage.setItem("player_session",d.player_session);
      location.href=d.redirect;
    }catch(err){error.textContent=err.message;error.classList.remove("hidden")}
  });
})();
