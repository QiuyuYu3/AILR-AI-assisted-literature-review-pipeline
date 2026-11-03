// Tags every callback with the project this page was loaded for; the server refuses one from a stale tab.
(function () {
  var realFetch = window.fetch;

  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    var tag = document.getElementById("project-tag");
    if (url.indexOf("_dash-update-component") === -1 || !tag) {
      return realFetch.apply(this, arguments);
    }
    var opts = Object.assign({}, init);
    var headers = new Headers(opts.headers || {});
    headers.set("X-Ailr-Project", tag.getAttribute("data-project") || "");
    opts.headers = headers;
    return realFetch.call(this, input, opts).then(function (response) {
      if (response.status === 409) {
        showStaleNotice();
      }
      return response;
    });
  };

  function showStaleNotice() {
    if (document.getElementById("stale-project-notice")) {
      return;
    }
    var box = document.createElement("div");
    box.id = "stale-project-notice";
    box.className = "alert alert-danger shadow d-flex align-items-center gap-3 m-0";
    box.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:2000;border-radius:0;";
    var text = document.createElement("span");
    text.textContent =
      "Another tab has opened a different project, so nothing you do on this page is saved. " +
      "Reload to continue with the project that is open now.";
    var button = document.createElement("button");
    button.className = "btn btn-sm btn-light";
    button.textContent = "Reload";
    button.onclick = function () {
      window.location.reload();
    };
    box.appendChild(text);
    box.appendChild(button);
    document.body.appendChild(box);
  }
})();
