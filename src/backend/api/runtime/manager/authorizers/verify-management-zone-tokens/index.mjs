import * as common from "./common/common.mjs";

export const handler = async (event) => {
    const body = event["body"];

    if (!body["id_token"] && !body["access_token"]) {
        return {};
    }

    const response = {};

    // Checks if the id_token and access_tokens are valid
    if (body["id_token"]) {
        console.log("id_token valid");
        const idTokenResponse = await common.verifyIdToken(body["id_token"]);
        response["id_token"] = { status: idTokenResponse.verificationStatus, body: idTokenResponse.verificationDescription };
    } else {
        console.log("id_token invalid");
    }
    if (body["access_token"]) {
        console.log("access_token valid");
        const accessTokenResponse = await common.verifyAccessToken(body["access_token"]);
        response["access_token"] = { status: accessTokenResponse.verificationStatus, body: accessTokenResponse.verificationDescription };
    } else {
        console.log("access_token invalid");
    }

    return response;
};
