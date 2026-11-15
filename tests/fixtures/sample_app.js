// Sample first-party app file used by inspector tests.
const DEBUG = true;
const featureFlags = { newCheckout: true };

function renderUserBio(html) {
    document.getElementById("bio").innerHTML = html;
}

function unsafeLoad(src) {
    eval(src);
}

if (user.isAdmin) {
    showAdminPanel();
}

function loadProfile(id) {
    return fetch("/api/users/" + id, {
        method: "GET",
        headers: { Authorization: "Bearer " + getToken() }
    });
}

function deleteUser(id) {
    return fetch("/api/users/" + id, { method: "DELETE" });
}

window.addEventListener("message", (e) => {
    handle(e.data);
});
