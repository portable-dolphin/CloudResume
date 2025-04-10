import * as common from "./common/common.mjs";

export const apiHandler = async (event, context, callback) => {
    try {
        if (Object.keys(event).includes("keep-alive")) {
            let verifiersLoaded = common.jwtVerifiersLoaded["id"] && common.jwtVerifiersLoaded["access"];
            if (!verifiersLoaded) {
                let verified = common.verifyIdToken("none");
                verified = common.verifyAccessToken("none");
            }
            console.log(`Kept alive - verifiers ${verifiersLoaded ? "were" : "were not"} loaded`);
            callback(null);
        }

        const principal = event.methodArn;
        var policy;

        const response = await common.verifyIdToken(event["authorizationToken"]);
        const verificationStatus = response.verificationStatus;
        const verificationDescription = response.verificationDescription;
        if (verificationStatus != 200) {
            console.log(`Bad session. Status code: ${verificationStatus} - Status reason: ${verificationDescription}`);
            policy = generatePolicy("user", "Deny", principal);
        } else {
            console.log("authorizing user");
            policy = generatePolicy("user", "Allow", principal);
        }

        callback(null, policy);
    } catch (e) {
        console.log(`Error: ${e.name} - ${e.message}`);
    }
};

function generatePolicy(principalId, effect, resource) {
    var authResponse = {};

    authResponse.principalId = principalId;
    if (effect && resource) {
        var policyDocument = {};
        policyDocument.Version = "2012-10-17";
        policyDocument.Statement = [];
        var statementOne = {};
        statementOne.Action = "execute-api:Invoke";
        statementOne.Effect = effect;
        statementOne.Resource = resource;
        policyDocument.Statement[0] = statementOne;
        authResponse.policyDocument = policyDocument;
    }

    return authResponse;
}
