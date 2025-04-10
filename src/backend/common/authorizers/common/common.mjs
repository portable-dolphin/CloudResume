import { CognitoJwtVerifier } from "aws-jwt-verify";
import { readFileSync } from "fs";
import { JwtExpiredError } from "aws-jwt-verify/error";
import { JwtParseError } from "aws-jwt-verify/error";
export const tokenResponses = {
    verified: {
        verificationStatus: "200",
        verificationDescription: "ok",
    },
    "verify-expired": {
        verificationStatus: "307",
        verificationDescription: "token_expired",
    },
    "verify-notInGroup": {
        verificationStatus: "403",
        verificationDescription: "forbidden",
    },
    "verify-badToken": {
        verificationStatus: "401",
        verificationDescription: "token_invalid",
    },
    unknown: {
        verificationStatus: "500",
        verificationDescription: "Internal Server Error",
    },
};
var config = getConfig();
export var jwtVerifiersLoaded = {
    id: false,
    access: false,
};
const jwtVerifiers = {};
// Returns a JwtVerifier object from the Cognito library
export async function getJwtVerifier(tokenType) {
    var jwtVerifier;
    if (Object.keys(jwtVerifiers).includes(tokenType)) {
        jwtVerifier = jwtVerifiers[tokenType];
    } else {
        jwtVerifier = CognitoJwtVerifier.create({
            userPoolId: config["userpool_id"],
            clientId: config["client_id"],
            tokenUse: tokenType,
            includeRawJwtInErrors: true,
        });
        jwtVerifiers[tokenType] = jwtVerifier;
        jwtVerifiersLoaded[tokenType] = true;
    }

    return jwtVerifier;
}

// Checks the provided token and token type against the Cognito server
async function verifyToken(token, tokenType) {
    var payload;
    if (!["id", "access"].includes(tokenType)) {
        return { tokenResponse: { ...tokenResponses["unknown"] }, payload: payload };
    }

    try {
        var jwtVerifier = await getJwtVerifier(tokenType);
        payload = await jwtVerifier.verify(token);
    } catch (err) {
        console.log("ERROR: JWT could not be parsed");
        console.log(err);
        if (err instanceof JwtExpiredError) {
            return { tokenResponse: { ...tokenResponses["verify-expired"] }, payload: payload };
        } else if (err instanceof JwtParseError) {
            return { tokenResponse: { ...tokenResponses["verify-badToken"] }, payload: payload };
        } else {
            return { tokenResponse: { ...tokenResponses["unknown"] }, payload: payload };
        }
    }
    return { tokenResponse: { ...tokenResponses["verified"] }, payload: payload };
}

export function getConfig() {
    config = JSON.parse(readFileSync(import.meta.dirname + "/config.json").toString("utf8"));
    return config;
}

export async function verifyAccessToken(accessToken) {
    const response = await verifyToken(accessToken, "access");
    return response.tokenResponse;
}

// Checks to see if the access token is valid, then checks to see if the user is in the correct group
export async function verifyIdToken(idToken) {
    const response = await verifyToken(idToken, "id");
    if (response.tokenResponse.verificationStatus != tokenResponses["verified"]["verificationStatus"]) {
        return response.tokenResponse;
    }
    var BreakException = { break: true };
    var inGroup = false;
    if (Object.keys(response["payload"]).includes("cognito:groups")) {
        try {
            response["payload"]["cognito:groups"].forEach((group) => {
                inGroup = config["authorized_groups"].includes(group);
                if (inGroup) {
                    throw BreakException;
                }
            });
        } catch (e) {
            if (e !== BreakException) {
                throw e;
            }
        }
    }
    if (inGroup) {
        return response.tokenResponse;
    } else {
        return tokenResponses["verify-notInGroup"];
    }
}
