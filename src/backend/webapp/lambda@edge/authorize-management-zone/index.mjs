import { parse } from "cookie";
import * as common from "./common/common.mjs";
const config = common.getConfig();

export const cfHandler = async (event) => {
    const request = event.Records[0].cf.request;
    const headers = request.headers;

    // The cookie "access_token" must be present. id_token and refresh_token may be present as well, but are not required
    const cookies = parseCookies(headers["cookie"]);
    var badSession = false,
        verificationDescription,
        verificationStatus;
    if (!cookies["id_token"]) {
        badSession = true;
        verificationStatus = 401;
        verificationDescription = "No id token present";
    } else {
        var response = await common.verifyIdToken(cookies["id_token"]);
        verificationStatus = response.verificationStatus;
        verificationDescription = response.verificationDescription;
        if (verificationStatus != 200) {
            badSession = true;
        }
    }

    console.log(verificationDescription);

    // Bad sessions (unauthorized, refresh token expired, etc.) return the status code and reason for being bad
    if (badSession) {
        const response = {
            status: verificationStatus,
            body: verificationDescription,
        };
        if (verificationStatus == common.tokenResponses["verify-expired"]["verificationStatus"]) {
            response["headers"] = {
                location: [
                    {
                        key: "Location",
                        value: config["redirect_url"],
                    },
                ],
            };
        }
        return response;
    }
    // Good sessions return the original request so CloudFront can continue with the request
    else if (verificationStatus == 200) {
        console.log("Session authenticated");
        return request;
    }
};

function parseCookies(cookieHeader) {
    if (!cookieHeader) {
        return {};
    }

    const cookiesString = cookieHeader[0]["value"];
    const cookies = parse(cookiesString);
    return cookies;
}
