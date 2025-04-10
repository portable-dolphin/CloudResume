export const config = {
    managerUrls: {
        base: "[BASE_DOMAIN_URL_PLACEHOLDER]",
        managerIndex: "/management-zone/manager/index.html",
        loginRedirect: "/management-zone/login-redirect.html",
        loginVerify: "/management-zone/login-verify.html",
        error401: "/management-zone/error401.html",
        error403: "/management-zone/error403.html",
        genericError: "/management-zone/error.html",
    },
    authenticatorUrls: {
        base: "[BASE_APP_MANAGEMENT_ZONE_URL_PLACEHOLDER]",
        oauth2Authorize: "/oauth2/authorize",
        oauth2Token: "/oauth2/token",
    },
    appManagementApiUrls: {
        base: "[BASE_APP_MANAGEMENT_API_URL_PLACEHOLDER]",
        apiValidator: "/v1/management-zone/validate",
    },
    cookieSettings: {
        cookiePath: "/management-zone/",
        cookieDomain: "[BASE_DOMAIN_NAME_PLACEHOLDER]",
    },
    clientId: "[COGNITO_CLIENT_ID_PLACEHOLDER]",
};

export const charset = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890";

export async function renewRefreshToken(refreshToken) {
    if (refreshToken === undefined) {
        throw new Error("refreshToken is a required parameter");
    }
    const headers = new Headers();
    headers.append("Content-Type", "application/x-www-form-urlencoded");
    const url = `${config["authenticatorUrls"]["base"]}${config["authenticatorUrls"]["oauth2Token"]}`;
    const bodyText = `client_id=${config["clientId"]}&` + `refresh_token=${refreshToken}&` + "grant_type=refresh_token&";
    const response = await fetch(url, {
        method: "POST",
        headers: headers,
        body: bodyText,
    });
    if (!response.ok) {
        const responseData = await response.text();
        if (response.status == 400) {
            const responseJson = JSON.parse(responseData);
            if (responseJson["error"] == "invalid_grant") {
                throw new Error("refresh_token_invalid");
            }
        }
        throw new Error("unknown_error");
    }
    var responseBody = await response.json();
    return { newIdToken: responseBody.id_token, newAccessToken: responseBody.access_token };
}

export function getCookieByName(name) {
    let i,
        cookieName,
        retCookie,
        cookies = document.cookie.split("; ");
    for (i = 0; i < cookies.length; i++) {
        cookieName = cookies[i].substr(0, cookies[i].indexOf("="));
        if (cookieName == name) {
            retCookie = cookies[i].substr(cookies[i].indexOf("=") + 1);
        }
    }
    return retCookie;
}

export async function verifyCookies(accessToken, refreshToken, idToken) {
    var redirectUrl;
    const headers = new Headers();
    headers.append("Content-Type", "application/json");
    const url = `${config["appManagementApiUrls"]["base"]}${config["appManagementApiUrls"]["apiValidator"]}`;
    const bodyText = '{"id_token": "' + idToken + '", "access_token": "' + accessToken + '"}';
    const response = await fetch(url, {
        method: "POST",
        headers: headers,
        body: bodyText,
    });
    if (!response.ok) {
        const text = await response.text();
        throw new Error(`Response status: ${response.status} - Response data: ${text} - Body text: ${bodyText}`);
    }
    const responseJson = await response.json();

    console.log(JSON.stringify(responseJson));

    // If the token is expired
    if ([responseJson["id_token"]["body"], responseJson["access_token"]["body"]].includes("token_expired")) {
        try {
            // Get new tokens with the refresh token
            var { newIdToken, newAccessToken } = await renewRefreshToken(refreshToken);
            set_token_cookie("id_token", newIdToken);
            set_token_cookie("access_token", newAccessToken);
        } catch (err) {
            console.log(`Error refreshing tokens. Redirecting to loginRedirect page. Error ${err.message}`);
            expireCookie("id_token", config["cookieSettings"]["cookiePath"]);
            expireCookie("access_token", config["cookieSettings"]["cookiePath"]);
            expireCookie("refresh_token", config["cookieSettings"]["cookiePath"]);
            redirectUrl = `${config["managerUrls"]["base"]}${config["managerUrls"]["loginRedirect"]}`;
        }
    }
    // If the token is invalid
    else if ([responseJson["id_token"]["body"], responseJson["access_token"]["body"]].includes("token_invalid")) {
        expireCookie("id_token", config["cookieSettings"]["cookiePath"]);
        expireCookie("access_token", config["cookieSettings"]["cookiePath"]);
        expireCookie("refresh_token", config["cookieSettings"]["cookiePath"]);
        redirectUrl = `${config["managerUrls"]["base"]}${config["managerUrls"]["loginRedirect"]}`;
    }
    // If the access token returned "forbidden" (user is not in the correct group)
    else if (responseJson["id_token"]["body"] == "forbidden") {
        redirectUrl = `${config["managerUrls"]["base"]}${config["managerUrls"]["error403"]}`;
    }
    // If the access token returned "token invalid" (token was malformed)
    else if (responseJson["id_token"]["body"] == "token_invalid") {
        redirectUrl = `${config["managerUrls"]["base"]}${config["managerUrls"]["error401"]}`;
    }
    // If the access token returned valid
    else if (responseJson["id_token"]["status"] == "200") {
        redirectUrl = `${config["managerUrls"]["base"]}${config["managerUrls"]["managerIndex"]}`;
    }
    // If some other response was given
    else {
        expireCookie("id_token", config["cookieSettings"]["cookiePath"]);
        expireCookie("access_token", config["cookieSettings"]["cookiePath"]);
        expireCookie("refresh_token", config["cookieSettings"]["cookiePath"]);
        expireCookie("verifier", "/");
        expireCookie("challenge", "/");
        redirectUrl = `${config["managerUrls"]["base"]}${config["managerUrls"]["genericError"]}`;
    }

    return redirectUrl;
}

export function expireCookie(cookieName, path) {
    document.cookie = `${cookieName}=""; expires=Thu, 01 Jan 1970 00:00:00 UTC; secure; path=${path}; SameSite=None; domain=${config["cookieSettings"]["cookieDomain"]}`;
}

export function set_token_cookie(cookieName, cookieValue) {
    const days = 3;
    let date = new Date();
    date.setDate(date.getDate() + days);
    let date_string = date.toUTCString();
    const expires = `expires=${date_string}`;
    document.cookie = `${cookieName}=${cookieValue}; expires=${expires}; secure; path=${config["cookieSettings"]["cookiePath"]}; SameSite=None; domain=${config["cookieSettings"]["cookieDomain"]}`;
}
