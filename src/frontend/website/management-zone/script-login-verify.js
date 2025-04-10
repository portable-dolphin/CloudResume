import * as common from "./common/common.js";

async function get_tokens() {
    const query_parameters = new Proxy(new URLSearchParams(window.location.search), {
        get: (searchParams, prop) => searchParams.get(prop),
    });
    const code = query_parameters.code;
    if (code != null) {
        let verifier = common.getCookieByName("verifier");
        if (verifier != null) {
            const headers = new Headers();
            headers.append("Content-Type", "application/x-www-form-urlencoded");
            const bodyText =
                `redirect_uri=${common.config["managerUrls"]["base"]}${common.config["managerUrls"]["loginVerify"]}&` +
                `client_id=${common.config["clientId"]}&` +
                `code=${code}&` +
                "grant_type=authorization_code&" +
                `code_verifier=${verifier}`;
            const response = await fetch(`${common.config["authenticatorUrls"]["base"]}${common.config["authenticatorUrls"]["oauth2Token"]}`, {
                method: "POST",
                headers: headers,
                body: bodyText,
            });
            if (!response.ok) {
                const text = await response.text();
                throw new Error(`Response status: ${response.status} - Response data: ${text} - Body text: ${bodyText}`);
            }
            var responseBody = await response.json();
            return { id_token: responseBody.id_token, access_token: responseBody.access_token, refresh_token: responseBody.refresh_token };
        }
    }
}

window.onload = async function () {
    var idToken = common.getCookieByName("id_token");
    var accessToken = common.getCookieByName("access_token");
    var refreshToken = common.getCookieByName("refresh_token");
    var redirectUrl;
    if (idToken !== undefined && accessToken !== undefined && refreshToken !== undefined) {
        redirectUrl = await common.verifyCookies(accessToken, refreshToken, idToken);
    } else {
        const verifierCookie = common.getCookieByName("verifier");
        if (verifierCookie !== undefined) {
            try {
                var { id_token, access_token, refresh_token } = await get_tokens();
            } catch (err) {
                console.log(err);
                redirectUrl = `${common.config["managerUrls"]["base"]}${common.config["managerUrls"]["genericError"]}`;
            }
            common.set_token_cookie("id_token", id_token);
            common.set_token_cookie("access_token", access_token);
            common.set_token_cookie("refresh_token", refresh_token);
            common.expireCookie("verifier", "/");
            common.expireCookie("challenge", "/");
        } else {
            redirectUrl = `${common.config["managerUrls"]["base"]}${common.config["managerUrls"]["loginRedirect"]}`;
        }
    }
    if (redirectUrl === undefined) {
        redirectUrl = `${common.config["managerUrls"]["base"]}${common.config["managerUrls"]["managerIndex"]}`;
    }
    console.log(redirectUrl);
    window.location.href = redirectUrl;
};
