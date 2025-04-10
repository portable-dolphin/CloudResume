import * as footerScript from "./include-footer.js";

const apiUrl = "[BASE_RESUME_API_URL_PLACEHOLDER]";
const apiPath = "/v1/resumes/get-view-count";

async function updateViewCounter() {
    const htmlFile = window.location.pathname.split("/").slice(-1).join("");
    const resumeId = htmlFile.split(".").slice(0, -1).join(".");
    const res = await getViewCount(resumeId);
    let viewCount = res.views;
    let numberPositions = getNumberPositons(viewCount);
    const positions = ["hundred-thousands", "ten-thousands", "thousands", "hundreds", "tens", "ones"];
    for (let i = 0; i < numberPositions.length; i++) {
        const viewCounter = document.getElementById("view-counter-" + positions[i]);
        viewCounter.innerHTML = numberPositions[i];
    }
}

async function getViewCount(resumeId) {
    const requestUrl = `${apiUrl}${apiPath}`;
    const requestData = { id: resumeId };
    const requestParams = {
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify(requestData),
        method: "POST",
    };
    try {
        const res = await fetch(requestUrl, requestParams);

        const obj = await res.json();

        return obj;
    } catch (error) {
        console.log(error);
    }
}

function getNumberPositons(number) {
    const numberPositions = [];
    var divisor = 100000;
    while (divisor > 0.1) {
        const tempNumber = Math.floor(number / divisor);
        numberPositions.push(tempNumber);
        number = number % divisor;
        divisor /= 10;
    }
    return numberPositions;
}

window.addEventListener("load", () => {
    footerScript.loadFooter();
    updateViewCounter();
});
