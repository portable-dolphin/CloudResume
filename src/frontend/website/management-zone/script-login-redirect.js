import { config } from "./common/common.js";
import { expireCookie } from "./common/common.js";
import { getCookieByName } from "./common/common.js";
import { charset } from "./common/common.js";

function GetURLSafeBase64(val) {
    return btoa(String.fromCharCode.apply(null, new Uint8Array(val))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function GetSHA256Hash(input) {
    const data = new TextEncoder().encode(input);
    const hash = await window.crypto.subtle.digest("SHA-256", data);
    return hash;
}

async function generate_verifier_and_challenge() {
    var verifier = "";
    const char_len = charset.length;
    const rand_count = 128;
    let i = 0;
    while (i < rand_count) {
        verifier += charset.charAt(Math.floor(Math.random() * char_len));
        i += 1;
    }
    const verifier_hash = await GetSHA256Hash(verifier);
    const challenge = GetURLSafeBase64(verifier_hash);
    return { "verifier": verifier, "challenge": challenge };
}

function set_verifier_cookie(verifier, challenge) {
    const days = 1;
    let date = new Date();
    date.setDate(date.getDate() + days);
    document.cookie = `verifier=${verifier}; expires=${date.toUTCString()}; secure; path=/; SameSite=Strict; domain=${config["cookieSettings"]["cookieDomain"]}`;
    document.cookie = `challenge=${challenge}; expires=${date.toUTCString()}; secure; path=/; SameSite=Strict; domain="${config["cookieSettings"]["cookieDomain"]}`;
}

window.onload = async function () {
    var idToken = getCookieByName("id_token");
    var accessToken = getCookieByName("access_token");
    var refreshToken = getCookieByName("refresh_token");
    var redirectUrl;
    // Expire the token if it's not a base64 string
    try {
        if (idToken !== undefined) {
            atob(idToken);
        }
    }
    catch {
        expireCookie("id_token", config["cookieSettings"]["cookiePath"]);
        idToken = undefined;
    }

    // Expire the token if it's not a base64 string
    try {
        if (accessToken !== undefined) {
            atob(accessToken);
        }
    }
    catch {
        expireCookie("access_token", config["cookieSettings"]["cookiePath"]);
        accessToken = undefined;
    }

    // Expire the token if it's not a base64 string
    try {
        if (refreshToken !== undefined) {
            atob(refreshToken);
        }
    }
    catch {
        expireCookie("refresh_token", config["cookieSettings"]["cookiePath"]);
        refreshToken = undefined;
    }

    // If at least the refresh token cookie is defined, redirect to the loginVerify page
    if (refreshToken !== undefined) {
        redirectUrl = `${config["managerUrls"]["base"]}${config["managerUrls"]["loginVerify"]}`;
    }
    // Otherwise, redirect to the login page
    else {
        const { verifier, challenge } = await generate_verifier_and_challenge();
        set_verifier_cookie(verifier, challenge);
        redirectUrl = `${config["authenticatorUrls"]["base"]}${config["authenticatorUrls"]["oauth2Authorize"]}` +
                      `?response_type=code` +
                      `&client_id=${config["clientId"]}` +
                      `&redirect_uri=${config["managerUrls"]["base"]}${config["managerUrls"]["loginVerify"]}` +
                      `&code_challenge=${challenge}&code_challenge_method=S256`;
    }
    window.location.href = redirectUrl;
};
