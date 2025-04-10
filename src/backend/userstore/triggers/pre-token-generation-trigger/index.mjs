export const handler = async (event) => {
    event.response = {
        claimsOverrideDetails: {
            claimsToAddOrOverride: {
                given_name: event.request.userAttributes.given_name,
            },
        },
    };
    console.log(JSON.stringify(event));
    return event;
};
