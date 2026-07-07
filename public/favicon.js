(function () {
  function setFavicon() {
    let link = document.querySelector("link[rel~='icon']");
    if (!link) {
      link = document.createElement("link");
      link.rel = "icon";
      document.head.appendChild(link);
    }
    link.href = "/public/favicon.png";
    link.type = "image/png";
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setFavicon);
  } else {
    setFavicon();
  }
})();
