export async function loadFooter() {
    const websiteBaseUrl = window.location.origin;
    const url = `${websiteBaseUrl}/resumes/common/footer.html`;
    const response = await fetch(url, { method: "GET" });
    const responseContentType = response.headers.get("content-type");
    if (responseContentType == "text/html") {
        const footerHTML = await response.text();
        const footer = new DOMParser().parseFromString(footerHTML, "text/html");
        document.querySelector("#footer-section").appendChild(footer.querySelector("p"));
    }
}
