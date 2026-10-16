/* Bundled component, no SDK/CDN, no parent DOM access and no provider credentials. */
"use strict";
const storageKey = "documind.cloud.v1";
const parentOrigin = window.location.origin;
// Never send a restored capability to a foreign site framing this component.
if (!document.referrer || new URL(document.referrer).origin !== parentOrigin) {
  throw new Error("Authentication requires a same-origin app frame.");
}
let lastValue = "";
function send(type, data) {
  window.parent.postMessage({isStreamlitMessage:true, type, ...data}, parentOrigin);
}
function value(data) {
  const serialized = JSON.stringify(data);
  if (serialized !== lastValue) {
    lastValue = serialized;
    send("streamlit:setComponentValue", {value:data, dataType:"json"});
  }
}
window.addEventListener("message", event => {
  if (event.source !== window.parent || event.origin !== parentOrigin || event.data.type !== "streamlit:render") return;
  const args = event.data.args;
  const link = document.getElementById("login");
  try {
    let saved = JSON.parse(sessionStorage.getItem(storageKey) || "{}");
    if (args.clear) {
      sessionStorage.removeItem(storageKey);
      link.hidden = true;
      value({cleared:true});
    } else if (args.save) {
      sessionStorage.setItem(storageKey, JSON.stringify({session:args.save}));
      link.hidden = true;
      value({session:args.save});
    } else if (args.callback) {
      link.hidden = true;
      if (saved.state !== args.callback || !saved.binding) value({error:true});
      else value({state:saved.state, binding:saved.binding});
    } else if (saved.session) {
      link.hidden = true;
      value({session:saved.session});
    } else if (args.flow) {
      const destination = new URL(args.flow.url);
      if (destination.origin !== args.auth_origin || destination.pathname !== "/auth/v1/authorize") throw new Error();
      link.href = destination.href;
      link.hidden = false;
      link.onclick = event => {
        event.preventDefault();
        // Streamlit forbids top navigation but supports escaping popups. Seed a
        // same-origin blank tab, sever its opener BEFORE visiting the provider,
        // then continue the entire callback/session in that new tab.
        const pending = JSON.stringify({state:args.flow.state, binding:args.flow.binding});
        const popup = window.open("about:blank", "_blank");
        if (!popup) {
          document.getElementById("notice").textContent = "Allow this sign-in tab to open, then try again.";
          return;
        }
        try {
          popup.sessionStorage.setItem(storageKey, pending);
          popup.opener = null;
          popup.location.replace(destination.href);
          document.getElementById("notice").textContent = "Continue in the new sign-in tab.";
        } catch (_) {
          popup.close();
          document.getElementById("notice").textContent = "A secure sign-in tab could not be opened.";
        }
      };
    }
  } catch (_) {
    link.hidden = true;
    document.getElementById("notice").textContent = "Browser session storage is unavailable. Sign-in cannot continue safely.";
    value({error:true});
  }
  send("streamlit:setFrameHeight", {height:64});
});
send("streamlit:componentReady", {apiVersion:1});
