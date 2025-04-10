import * as common from "../common/common.js";

var selectedItem = null;
var modifyingRow = false;
var addingResume = false;
var isViewActive = true;
const viewType = {
    true: "active",
    false: "deleted",
};
var resumes = {};
var jwtClaims = {};
const apiBaseUrl = "[BASE_APP_MANAGEMENT_API_URL_PLACEHOLDER]/v1/management-zone";
const apiPaths = {
    getAllResumes: { url: "/manager-backend/list-all-resumes", method: "POST" },
    getResume: { url: "/manager-backend/get-resume", method: "POST" },
    generatePresignedUrl: { url: "/manager-backend/generate-presigned-url", method: "POST" },
    addResume: { url: "/manager-backend/add-resume", method: "PUT" },
    updateResume: { url: "/manager-backend/update-resume", method: "PATCH" },
    deleteResume: { url: "/manager-backend/delete-resume", method: "DELETE" },
    undeleteResume: { url: "/manager-backend/undelete-resume", method: "POST" },
    permanentlyDeleteResume: { url: "/manager-backend/permanently-delete-resume", method: "DELETE" },
    getDanglingResumes: { url: "/manager-backend/get-dangling-resumes", method: "POST" },
    cleanupDanglingResumes: { url: "/manager-backend/delete-dangling-resumes", method: "DELETE" },
};
const elementIds = {
    addOrModifyResumeLabel: "#add-or-modify-resume-label",
    addResumeAction: "#add-resume-action",
    addResumeSelector: "#add-resume-selector",
    cancelAddModifyResumeAction: "#cancel-add-modify-resume-action",
    cleanupDanglingResumesAction: "#cleanup-dangling-resumes-action",
    clearSelectedResumeFileAction: "#clear-selected-resume-file-action",
    companyTextbox: "#company-textbox",
    confirmDivOverlay: "#confirm-div-overlay",
    confirmDivAdditionalParagraphs: "#confirm-div-additional-paragraphs",
    confirmParagraph: "#confirm-paragraph",
    confirmTitle: "#confirm-title",
    confirmNoAction: "#confirm-no-action",
    confirmYesAction: "#confirm-yes-action",
    deleteResumeAction: "#delete-resume-action",
    inputsInfoParagraph: "#inputs-info-paragraph",
    jobPostingTextbox: "#job-posting-textbox",
    jobTitleTextbox: "#job-title-textbox",
    modifyAddItemOuter: "#modify-add-item-outer",
    modifyResumeAction: "#modify-resume-action",
    modifyResumeSelector: "#modify-resume-selector",
    overlay: "#overlay",
    permanentlyDeleteResumeAction: "#permanently-delete-resume-action",
    refreshAllResumesAction: "#refresh-all-resumes-action",
    refreshResumeAction: "#refresh-resume-action",
    resumeFileTextbox: "#resume-file-textbox",
    resumeIdTextbox: "#resume-id-textbox",
    selectResumeFileInput: "#select-resume-file-input",
    selectResumeFileAction: "#select-resume-file-action",
    undeleteResumeAction: "#undelete-resume-action",
    viewActiveResumesSelector: "#view-active-resumes-selector",
    viewDeletedResumesSelector: "#view-deleted-resumes-selector",
};
const elementClasses = {
    addModifyInput: ".add-modify-input",
    disableWhileAddOrModify: ".disable-while-add-or-modify",
    valueRequiredModify: ".value-required-modify",
    valueRequiredAdd: ".value-required-add",
    disableOnLoad: ".disable-on-load",
    tableRow: ".table-row",
    activeResumeView: ".resume-view-active",
    deletedResumeView: ".resume-view-deleted",
};
const resumeParsePendingMessage = "RESUME_PARSE_PENDING";
const invalidatingCachePendingMessage = "CACHE_INVALIDATION_PENDING";
var fileSelector = document.querySelector(elementIds.selectResumeFileInput);
var fileReader, fileContent;
var confirmChoice = null;
const invalidIdCharactersRegex = /[^-a-zA-Z0-9_]/g;

async function getAllResumes() {
    var body, response;
    if (isViewActive) {
        body = "{}";
    } else {
        body = '{"deleted": true}';
    }

    response = await sendWebRequestWithAuth(apiBaseUrl + apiPaths["getAllResumes"]["url"], apiPaths["getAllResumes"]["method"], body);

    if (response == null) {
        return;
    }

    resumes[viewType[isViewActive]] = {};

    if (response["body"].constructor != Array) {
        if (response["body"] != "") {
            setStatusElementText(`Unable to retrieve all resumes, server returned an unknown response. Please contact an administrator for more assistance.`);
            return;
        }
    } else {
        for (var i = 0; i < response["body"].length; i++) {
            let resumeItem = {
                id: response["body"][i]["id"],
                company: response["body"][i]["company"],
                jobTitle: response["body"][i]["job_title"],
                jobPosting: response["body"][i]["job_posting"],
                resumeUrl: response["body"][i]["resume_url"],
                dateCreated: response["body"][i]["date_created"],
                views: response["body"][i]["view_count"],
            };

            resumes[viewType[isViewActive]][response["body"][i]["id"]] = resumeItem;
        }
    }

    await populateTable();
}

async function getResume() {
    const resumeId = selectedItem;
    var data = { id: resumeId };
    if (!isViewActive) {
        data["deleted"] = true;
    }
    const response = await sendWebRequestWithAuth(apiBaseUrl + apiPaths["getResume"]["url"], apiPaths["getResume"]["method"], JSON.stringify(data));
    if (response == null) {
        return;
    }
    var resume = {
        id: response["body"]["id"],
        company: response["body"]["company"],
        jobTitle: response["body"]["job_title"],
        jobPosting: response["body"]["job_posting"],
        resumeUrl: response["body"]["resume_url"],
        dateCreated: response["body"]["date_created"],
        views: response["body"]["view_count"],
    };

    resumes[viewType[isViewActive]][response["body"]["id"]] = resume;

    addResumeToRow(resume["id"], resume["company"], resume["jobTitle"], resume["jobPosting"], resume["resumeUrl"], resume["dateCreated"], resume["views"]);
}

async function deleteOrUndeleteResume() {
    const resumeId = selectedItem;
    const data = `{"id": "${resumeId}"}`;
    var operation = isViewActive ? "delete" : "undelete";
    const url = apiBaseUrl + apiPaths[`${operation}Resume`]["url"];
    const method = apiPaths[`${operation}Resume`]["method"];

    const response = await sendWebRequestWithAuth(url, method, data);
    if (response != null) {
        if (response.status == 200) {
            var resume = {
                id: response["body"]["id"],
                company: response["body"]["company"],
                jobTitle: response["body"]["job_title"],
                jobPosting: response["body"]["job_posting"],
                resumeUrl: response["body"]["resume_url"],
                dateCreated: response["body"]["date_created"],
                views: response["body"]["view_count"],
            };
            resumes[viewType[isViewActive]][response["body"]["id"]] = resume;
            addResumeToRow(resume["id"], resume["company"], resume["jobTitle"], resume["jobPosting"], resume["resumeUrl"], resume["dateCreated"], resume["views"]);
            if (Object.keys(resumes).includes(viewType[!isViewActive])) {
                resumes[viewType[!isViewActive]][resumeId] = resumes[viewType[isViewActive]][resumeId];
            }
            delete resumes[viewType[isViewActive]][resumeId];
            populateTable();
        } else if (response.status == 404) {
            setStatusElementText(`A resume with the ID of ${resumeId} could not be found. Please contact an administrator for more assistance.`);
        } else {
            setStatusElementText(`There was an unknown error when attempting to ${operation} the resume. Please contact an administrator for more assistance.`);
        }
    }
}

async function permanentlyDeleteResume() {
    const resumeId = selectedItem;
    const choice = await getChoice("Permanently Delete Resume", "Are you sure you want to permanently delete the selected resume?");
    if (!choice) {
        return;
    }

    const data = { id: resumeId };
    const response = await sendWebRequestWithAuth(apiBaseUrl + apiPaths["permanentlyDeleteResume"]["url"], apiPaths["permanentlyDeleteResume"]["method"], JSON.stringify(data));
    if (response == null) {
        setStatusElementText("Unable to permanently delete the resume. Server returned an unknown response.");
    } else if (response.status == 404) {
        setStatusElementText("Unable to permanently delete the resume. Resume could not be found.");
    } else if (response.status != 200) {
        setStatusElementText("Unable to permanently delete the resume. Server returned an unknown response.");
    } else {
        delete resumes[viewType[isViewActive]][resumeId];
        populateTable();
    }
}

async function switchResumeView() {
    const oldView = viewType[isViewActive];
    const newView = viewType[!isViewActive];
    const oldViewElements = document.querySelectorAll(elementClasses[`${oldView}ResumeView`]);
    const newViewElements = document.querySelectorAll(elementClasses[`${newView}ResumeView`]);
    for (let i = 0; i < oldViewElements.length; i++) {
        oldViewElements[i].style.display = "none";
    }
    for (let i = 0; i < newViewElements.length; i++) {
        newViewElements[i].style.display = "flex";
    }
    isViewActive = !isViewActive;

    await refreshAllResumes();
}

async function refreshResume() {
    addResumeToRow(selectedItem, "", "", "", "", "", "", false);
    getResume();
}

async function refreshAllResumes() {
    const refreshAllResumesSelector = document.querySelector(elementIds["refreshAllResumesAction"]);
    const switchResumeSelector = document.querySelector(elementIds[isViewActive ? "viewDeletedResumesSelector" : "viewActiveResumesSelector"]);
    refreshAllResumesSelector.disabled = true;
    switchResumeSelector.disabled = true;
    selectRow();
    clearTable();
    addResumeToRow("Refreshing Resumes...", "", "", "", "", "", "", false);
    await getAllResumes();
    refreshAllResumesSelector.disabled = false;
    switchResumeSelector.disabled = false;
}

async function loadResumeFile(element) {
    var file = element.target.files[0];
    if (file.name != "") {
        fileReader = new FileReader();
        fileReader.addEventListener("load", (readerEvent) => {
            fileContent = readerEvent.target.result;
        });
        fileReader.readAsArrayBuffer(file);
        document.querySelector(elementIds["resumeFileTextbox"]).value = file.name;

        // Check to see if the add or modify resume button should be enabled
        checkIfAddUpdateInputsHaveValue();
    }
}

function clearSelectedResumeFile() {
    document.querySelector(elementIds["resumeFileTextbox"]).value = "";
    fileSelector.value = "";
    fileContent = undefined;
    checkIfAddUpdateInputsHaveValue();
}

async function updateResume() {
    const resumeId = selectedItem;
    const companyValue = document.querySelector(elementIds["companyTextbox"]).value;
    const jobTitleValue = document.querySelector(elementIds["jobTitleTextbox"]).value;
    const jobPostingValue = document.querySelector(elementIds["jobPostingTextbox"]).value;
    const resumeFileValue = document.querySelector(elementIds["resumeFileTextbox"]).value;
    const infoParagraph = document.querySelector(elementIds["inputsInfoParagraph"]);
    const modifyResumeButton = document.querySelector(elementIds["modifyResumeAction"]);
    const updateAttributes = { id: resumeId };
    var resumeItem = {};
    if (resumes[viewType[isViewActive]][resumeId]["company"] != companyValue) {
        updateAttributes["company"] = companyValue;
    }
    if (resumes[viewType[isViewActive]][resumeId]["jobTitle"] != jobTitleValue) {
        updateAttributes["job_title"] = jobTitleValue;
    }
    if (resumes[viewType[isViewActive]][resumeId]["jobPosting"] != jobPostingValue) {
        updateAttributes["job_posting"] = jobPostingValue;
    }
    if (!jobPostingValue.toLowerCase().startsWith("https://")) {
        infoParagraph.innerText = `Job posting is invalid. It must begin with "https://"`;
        return;
    }

    if (Object.keys(updateAttributes).length == 1 && resumeFileValue == "") {
        if (resumeFileValue == "") {
            infoParagraph.innerText = "The company, job title, job posting, and resume file were not changed.";
            return;
        }
    } else {
        infoParagraph.innerText = "Updating, please wait...";
        modifyResumeButton.disabled = true;

        if (resumeFileValue != "") {
            const dateCreated = resumes[viewType[isViewActive]][resumeId]["dateCreated"];
            const views = resumes[viewType[isViewActive]][resumeId]["views"];
            const infoParagraph = document.querySelector(elementIds["inputsInfoParagraph"]);
            resumeItem = {
                id: resumeId,
                company: companyValue,
                jobTitle: jobTitleValue,
                jobPosting: jobPostingValue,
                resumeUrl: resumeParsePendingMessage,
                dateCreated: dateCreated,
                views: views,
            };

            const presignedUrlResponse = await generatePresignedUrl(resumeId);
            if (presignedUrlResponse != null && presignedUrlResponse.status == 200) {
                const url = presignedUrlResponse.body.url;
                console.log(url);
                const headers = presignedUrlResponse.body.headers;
                console.log(headers);
                const uploadResumeResponse = await sendWebRequest(url, "PUT", fileContent, headers);
                if (uploadResumeResponse == null || uploadResumeResponse.status !== 200) {
                    infoParagraph.innerText = "Resume upload failed. Item was not updated.";
                    return;
                }

                new Promise(async (ret) => {
                    var newUrl = await checkForResumeUrlUpdates(resumeId, resumeParsePendingMessage);
                    if (newUrl == invalidatingCachePendingMessage) {
                        checkForResumeUrlUpdates(resumeId, invalidatingCachePendingMessage);
                    }
                });
            } else if (presignedUrlResponse != null && presignedUrlResponse.status == "409") {
                infoParagraph.innerText = "A cache invalidation is still in progress. Please wait until it finishes.";
                return;
            } else {
                return;
            }
        }
        if (Object.keys(updateAttributes).length > 1) {
            try {
                const updateAttributesJson = JSON.stringify(updateAttributes);
                const response = await sendWebRequestWithAuth(apiBaseUrl + apiPaths["updateResume"]["url"], apiPaths["updateResume"]["method"], updateAttributesJson);

                if (resumeFileValue == "" && response != null && response.status == "200") {
                    resumeItem = {
                        id: response["body"]["id"],
                        company: response["body"]["company"],
                        jobTitle: response["body"]["job_title"],
                        jobPosting: response["body"]["job_posting"],
                        resumeUrl: response["body"]["resume_url"],
                        dateCreated: response["body"]["date_created"],
                        views: response["body"]["view_count"],
                    };
                }
            } finally {
                modifyResumeButton.disabled = false;
            }
        }
    }

    resumes[viewType[isViewActive]][resumeId] = resumeItem;

    addResumeToRow(
        resumeItem["id"],
        resumeItem["company"],
        resumeItem["jobTitle"],
        resumeItem["jobPosting"],
        resumeItem["resumeUrl"],
        resumeItem["dateCreated"],
        resumeItem["views"],
    );

    clearAddUpdateInputs();
    changeAddModifyDisplay("modify", "hide");
}

async function addResume() {
    const infoParagraph = document.querySelector(elementIds["inputsInfoParagraph"]);
    const addResumeButton = document.querySelector(elementIds["addResumeAction"]);
    const idValue = document.querySelector(elementIds["resumeIdTextbox"]).value;
    const companyValue = document.querySelector(elementIds["companyTextbox"]).value;
    const jobTitleValue = document.querySelector(elementIds["jobTitleTextbox"]).value;
    const jobPostingValue = document.querySelector(elementIds["jobPostingTextbox"]).value;

    if (fileContent === undefined || fileContent == "") {
        infoParagraph.innerText = "Resume file is empty or the contents could not be loaded. Please clear the file and try again.";
        return;
    }

    const idValueInvalidCharacters = new Set();
    var matches = [...idValue.matchAll(invalidIdCharactersRegex)];
    for (let i = 0; i < matches.length; i++) {
        idValueInvalidCharacters.add(`"${matches[i][0]}"`);
    }

    if (idValueInvalidCharacters.size) {
        infoParagraph.innerText = `ID contains the following invalid characters: ${[...idValueInvalidCharacters].join(", ")}`;
        return;
    }

    if (!jobPostingValue.toLowerCase().startsWith("https://")) {
        infoParagraph.innerText = `Job posting is invalid. It must begin with "https://"`;
        return;
    }

    const BadResponseException = { badResponse: true };
    try {
        const inputStatusMessage = "Submitting, please wait...";
        const data = { id: idValue, company: companyValue, job_title: jobTitleValue, job_posting: jobPostingValue };
        infoParagraph.innerText = inputStatusMessage;
        addResumeButton.disabled = true;
        const addResumeResponse = await sendWebRequestWithAuth(apiBaseUrl + apiPaths["addResume"]["url"], apiPaths["addResume"]["method"], JSON.stringify(data));
        if (addResumeResponse != null) {
            if (addResumeResponse.status == "200") {
                let resumeItem = {
                    id: addResumeResponse["body"]["id"],
                    company: addResumeResponse["body"]["company"],
                    jobTitle: addResumeResponse["body"]["job_title"],
                    jobPosting: addResumeResponse["body"]["job_posting"],
                    resumeUrl: addResumeResponse["body"]["resume_url"],
                    dateCreated: addResumeResponse["body"]["date_created"],
                    views: addResumeResponse["body"]["view_count"],
                };

                resumes[viewType[isViewActive]][addResumeResponse["body"]["id"]] = resumeItem;
            } else if (addResumeResponse.status == "409") {
                infoParagraph.innerText = "Resume appears to already exist.";
                addResumeButton.disabled = false;
                return;
            } else {
                throw BadResponseException;
            }
        } else {
            return;
        }

        const presignedUrlResponse = await generatePresignedUrl(idValue);
        if (presignedUrlResponse != null && presignedUrlResponse.status == 200) {
            const url = presignedUrlResponse.body.url;
            console.log(url);
            const headers = presignedUrlResponse.body.headers;
            console.log(headers);
            const uploadResumeResponse = await sendWebRequest(url, "PUT", fileContent, headers);
            if (uploadResumeResponse == null || uploadResumeResponse.status !== 200) {
                throw BadResponseException;
            }
            new Promise(async (ret) => {
                var newUrl = await checkForResumeUrlUpdates(idValue, resumeParsePendingMessage);
                if (newUrl == invalidatingCachePendingMessage) {
                    checkForResumeUrlUpdates(idValue, invalidatingCachePendingMessage);
                }
            });
            populateTable();
            changeAddModifyDisplay("add", "hide");
        } else {
            throw BadResponseException;
        }
    } catch (e) {
        addResumeButton.disabled = true;
        if (e === BadResponseException) {
            const data = { id: idValue };
            const response = await sendWebRequestWithAuth(
                apiBaseUrl + apiPaths["permanentlyDeleteResume"]["url"],
                apiPaths["permanentlyDeleteResume"]["method"],
                JSON.stringify(data),
            );
            if (response == null) {
                infoParagraph.innerText = "WARNING: Resume upload failed. Resume was partially uploaded, but could not be fully cleaned up.";
            } else if (response.status == 200 || response.status == 404) {
                infoParagraph.innerText = "Unable to add resume. Server returned an unknown response.";
            }
        } else {
            throw e;
        }
    }
}

async function generatePresignedUrl(id) {
    if (id === undefined) {
        throw new Error("id is a required parameter");
    } else if (id.constructor != String) {
        throw new Error("id must be a string");
    }
    const data = { id: id };

    const response = await sendWebRequestWithAuth(apiBaseUrl + apiPaths["generatePresignedUrl"]["url"], apiPaths["generatePresignedUrl"]["method"], JSON.stringify(data));

    return response;
}

async function checkForResumeUrlUpdates(resumeId, oldResumeUrl) {
    const retries = 30,
        sleepTime = 3000;
    var newResumeUrl;
    for (let i = 0; i < retries; i++) {
        await new Promise((ret) => setTimeout(ret, sleepTime));
        const data = { id: resumeId };
        const response = await sendWebRequestWithAuth(apiBaseUrl + apiPaths["getResume"]["url"], apiPaths["getResume"]["method"], JSON.stringify(data));
        console.log(response);
        if (response != null) {
            if (response.status == 200) {
                if (Object.keys(response["body"]).includes("resume_url") && oldResumeUrl != response["body"]["resume_url"]) {
                    let resumeItem = {
                        id: response["body"]["id"],
                        company: response["body"]["company"],
                        jobTitle: response["body"]["job_title"],
                        jobPosting: response["body"]["job_posting"],
                        resumeUrl: response["body"]["resume_url"],
                        dateCreated: response["body"]["date_created"],
                        views: response["body"]["view_count"],
                    };

                    resumes[viewType[isViewActive]][response["body"]["id"]] = resumeItem;
                    await addResumeToRow(
                        resumeItem["id"],
                        resumeItem["company"],
                        resumeItem["jobTitle"],
                        resumeItem["jobPosting"],
                        resumeItem["resumeUrl"],
                        resumeItem["dateCreated"],
                        resumeItem["views"],
                    );
                    newResumeUrl = resumeItem["resumeUrl"];
                    break;
                }
            } else {
                console.log(`Couldn't update resume URL with ID of ${resumeId}`);
                break;
            }
        }
    }

    return newResumeUrl;
}

async function cleanupDanglingResumes() {
    const responseDanglingResumes = await sendWebRequestWithAuth(apiBaseUrl + apiPaths["getDanglingResumes"]["url"], apiPaths["getDanglingResumes"]["method"], "{}");

    if (responseDanglingResumes == null || responseDanglingResumes.status != 200) {
        setStatusElementText(`There was an unknown error when attempting to get dangling resumes. Please contact an administrator for more assistance.`);
        return;
    }

    if (responseDanglingResumes.body.items == "0" && responseDanglingResumes.body.objects == "0") {
        alert("There are no dangling resumes to cleanup");
        return;
    }

    const choice = await getChoice(
        "Cleanup Dangling Resumes",
        "Are you sure you want to clean up all dangling resumes? Any unpaired database item and S3 object will be deleted.",
        "If you choose to proceed, the following will be deleted",
        `Database entries: ${responseDanglingResumes.body.items}`,
        `Resume files: ${responseDanglingResumes.body.objects}`,
    );
    if (!choice) {
        return;
    }

    const responseDeleteDanglingResumes = await sendWebRequestWithAuth(apiBaseUrl + apiPaths["cleanupDanglingResumes"]["url"], apiPaths["cleanupDanglingResumes"]["method"], "{}");

    if (responseDeleteDanglingResumes == null || responseDeleteDanglingResumes.status != 200) {
        if (responseDanglingResumes == null || responseDanglingResumes.status != 200) {
            setStatusElementText(`There was an unknown error when attempting to delete dangling resumes. Please contact an administrator for more assistance.`);
            return;
        }
    }

    alert(`Deleted ${responseDeleteDanglingResumes.body.items} database entries and ${responseDeleteDanglingResumes.body.objects} resume files`);
}

function clearAddUpdateInputs() {
    const inputs = document.querySelectorAll(elementClasses["addModifyInput"]);

    for (let i = 0; i < inputs.length; i++) {
        inputs[i].value = "";
    }

    fileSelector.value = "";
}

function changeAddModifyDisplay(action, display) {
    const resumeId = selectedItem;
    if (action === undefined) {
        throw new Error("action is a required parameter");
    }
    if (action.constructor != String || !["add", "modify"].includes(action)) {
        throw new Error("action must be either 'add' or 'modify'");
    }
    if (display === undefined) {
        throw new Error("display is a required parameter");
    }
    if (display.constructor != String || !["show", "hide"].includes(display)) {
        throw new Error("display must be either 'show' or 'hide'");
    }

    const modifyAddItemOuter = document.querySelector(elementIds["modifyAddItemOuter"]);
    const addOrModifyLabel = document.querySelector(elementIds["addOrModifyResumeLabel"]);
    const modifyResumeSelector = document.querySelector(elementIds["modifyResumeSelector"]);
    const selectedRow = document.querySelector(getRowId(resumeId, true));

    if (display == "show") {
        const disableWhileAddOrModifyButtons = document.querySelectorAll(elementClasses["disableWhileAddOrModify"]);
        const modifyResumeActionButton = document.querySelector(elementIds["modifyResumeAction"]);
        const addResumeActionButton = document.querySelector(elementIds["addResumeAction"]);
        const resumeIdTextbox = document.querySelector(elementIds["resumeIdTextbox"]);

        modifyResumeSelector.disabled = true;
        for (var i = 0; i < disableWhileAddOrModifyButtons.length; i++) {
            disableWhileAddOrModifyButtons[i].disabled = true;
        }
        modifyAddItemOuter.style.display = "flex";

        if (action == "add") {
            addOrModifyLabel.innerText = "Add Resume";
            modifyResumeActionButton.style.display = "none";
            addResumeActionButton.style.display = "";
            resumeIdTextbox.disabled = false;
            addingResume = true;
        } else {
            addOrModifyLabel.innerText = "Modify Resume";
            selectedRow.classList.add("modifying-row");
            if (Object.keys(resumes[viewType[isViewActive]][resumeId]).includes("id")) {
                resumeIdTextbox.value = resumes[viewType[isViewActive]][resumeId]["id"];
            }
            if (Object.keys(resumes[viewType[isViewActive]][resumeId]).includes("company")) {
                document.querySelector(elementIds["companyTextbox"]).value = resumes[viewType[isViewActive]][resumeId]["company"];
            }
            if (Object.keys(resumes[viewType[isViewActive]][resumeId]).includes("jobTitle")) {
                document.querySelector(elementIds["jobTitleTextbox"]).value = resumes[viewType[isViewActive]][resumeId]["jobTitle"];
            }
            if (Object.keys(resumes[viewType[isViewActive]][resumeId]).includes("jobPosting")) {
                document.querySelector(elementIds["jobPostingTextbox"]).value = resumes[viewType[isViewActive]][resumeId]["jobPosting"];
            }
            addResumeActionButton.style.display = "none";
            modifyResumeActionButton.style.display = "";
            modifyResumeActionButton.disabled = false;
            resumeIdTextbox.disabled = true;
            modifyingRow = true;
        }
    } else {
        const addResumeSelector = document.querySelector(elementIds["addResumeSelector"]);
        const refreshAllResumesSelector = document.querySelector(elementIds["refreshAllResumesAction"]);
        const viewDeletedResumesSelector = document.querySelector(elementIds["viewDeletedResumesSelector"]);
        const deleteResumeSelector = document.querySelector(elementIds["deleteResumeAction"]);
        const infoParagraph = document.querySelector(elementIds["inputsInfoParagraph"]);

        addResumeSelector.disabled = false;
        refreshAllResumesSelector.disabled = false;
        viewDeletedResumesSelector.disabled = false;
        if (resumeId != null) {
            selectedRow.classList.remove("modifying-row");
            modifyResumeSelector.disabled = false;
            deleteResumeSelector.disabled = false;
        }

        modifyAddItemOuter.style.display = "none";
        addOrModifyLabel.innerText = "";
        infoParagraph.innerText = "";

        clearAddUpdateInputs();
        clearSelectedResumeFile();
        modifyingRow = false;
        addingResume = false;
    }
}

function checkIfAddUpdateInputsHaveValue() {
    var inputClass;
    if (modifyingRow) {
        inputClass = elementClasses["valueRequiredModify"];
    } else {
        inputClass = elementClasses["valueRequiredAdd"];
    }
    const inputs = document.querySelectorAll(inputClass);
    const modifyResumeButton = document.querySelector(elementIds["modifyResumeAction"]);
    const addResumeButton = document.querySelector(elementIds["addResumeAction"]);
    var hasValue = 0;
    for (let i = 0; i < inputs.length; i++) {
        if (inputs[i].value !== undefined && inputs[i].value != "") {
            hasValue += 1;
        }
    }

    if (hasValue == inputs.length && (addResumeButton.disabled || modifyResumeButton.disabled)) {
        addResumeButton.disabled = false;
        modifyResumeButton.disabled = false;
    } else if (hasValue != inputs.length && (!addResumeButton.disabled || !modifyResumeButton.disabled)) {
        addResumeButton.disabled = true;
        modifyResumeButton.disabled = true;
    }
}

async function populateTable() {
    selectRow();
    clearTable();
    if (resumes[viewType[isViewActive]] === undefined) {
        addResumeToRow("Loading Resumes...", "", "", "", "", "", "", false);
        await getAllResumes();
        clearTable();
    }

    const sortedResumes = Object.values(resumes[viewType[isViewActive]]).sort((a, b) => {
        a["id"].localeCompare(b["id"]);
    });

    for (const resume of sortedResumes) {
        const id = resume["id"] === undefined || resume["id"] == null ? "" : resume["id"];
        const company = resume["company"] === undefined || resume["company"] == null ? "" : resume["company"];
        const jobTitle = resume["jobTitle"] === undefined || resume["jobTitle"] == null ? "" : resume["jobTitle"];
        const jobPosting = resume["jobPosting"] === undefined || resume["jobPosting"] == null ? "" : resume["jobPosting"];
        const resumeUrl = resume["resumeUrl"] === undefined || resume["resumeUrl"] == null ? "" : resume["resumeUrl"];
        const dateCreated = resume["dateCreated"] === undefined || resume["dateCreated"] == null ? "" : resume["dateCreated"];
        const views = resume["views"] === undefined || resume["views"] == null ? "" : resume["views"];
        addResumeToRow(id, company, jobTitle, jobPosting, resumeUrl, dateCreated, views);
    }
}

function clearTable() {
    var rows;
    rows = document.querySelectorAll(elementClasses["tableRow"]);
    if (rows.length > 0) {
        let numRows = rows.length;
        for (var i = 0; i < numRows; i++) {
            rows[i].remove();
        }
    }
}

function addResumeToRow(id, company, jobTitle, jobPosting, resumeUrl, dateCreated, views, selectable) {
    if (id === undefined) {
        throw new Error("id is a required parameter");
    }
    if (company === undefined) {
        throw new Error("company is a required parameter");
    }
    if (jobTitle === undefined) {
        throw new Error("jobTitle is a required parameter");
    }
    if (jobPosting === undefined) {
        throw new Error("jobPosting is a required parameter");
    }
    if (resumeUrl === undefined) {
        throw new Error("resumeUrl is a required parameter");
    }
    if (dateCreated === undefined) {
        throw new Error("dateCreated is a required parameter");
    }
    if (views === undefined) {
        throw new Error("views is a required parameter");
    }
    if (selectable === undefined) {
        selectable = true;
    } else {
        if (selectable.constructor != Boolean) {
            throw new Error("selectable must be a boolean");
        }
    }

    var cellId, cellCompany, cellJobTitle, cellJobPosting, cellResumeUrl, cellDateCreated, cellViews, jobPostingA, resumeUrlA;

    var table = document.getElementById("table-body");

    const rowId = getRowId(id);

    var existingRow = document.getElementById(rowId);

    if (existingRow == null) {
        var row = table.insertRow(-1);

        row.id = getRowId(id);
        row.className = "table-row";
        if (selectable) {
            row.addEventListener("click", () => selectRow(id));
        }

        cellId = row.insertCell(0);
        cellId.id = `${rowId}-id`;
        cellCompany = row.insertCell(1);
        cellCompany.id = `${rowId}-company`;
        cellJobTitle = row.insertCell(2);
        cellJobTitle.id = `${rowId}-job-title`;
        cellJobPosting = row.insertCell(3);
        cellJobPosting.id = `${rowId}-job-posting`;
        cellResumeUrl = row.insertCell(4);
        cellResumeUrl.id = `${rowId}-resume-url`;
        cellDateCreated = row.insertCell(5);
        cellDateCreated.id = `${rowId}-date-created`;
        cellViews = row.insertCell(6);
        cellViews.id = `${rowId}-views`;
    } else {
        cellId = document.getElementById(`${rowId}-id`);
        cellCompany = document.getElementById(`${rowId}-company`);
        cellJobTitle = document.getElementById(`${rowId}-job-title`);
        cellJobPosting = document.getElementById(`${rowId}-job-posting`);
        cellResumeUrl = document.getElementById(`${rowId}-resume-url`);
        cellDateCreated = document.getElementById(`${rowId}-date-created`);
        cellViews = document.getElementById(`${rowId}-views`);
    }

    cellId.innerText = id;
    cellCompany.innerText = company;
    cellJobTitle.innerText = jobTitle;

    jobPostingA = document.getElementById(`${rowId}-job-posting-a`);
    if (jobPosting != "") {
        if (existingRow == null || jobPostingA == null) {
            jobPostingA = document.createElement("a");
            jobPostingA.id = `${rowId}-job-posting-a`;
            jobPostingA.innerText = "Job Posting";
            cellJobPosting.appendChild(jobPostingA);
        }
        jobPostingA.href = jobPosting;
    } else {
        if (jobPostingA == null) {
            cellJobPosting.innerText = "";
        } else {
            jobPostingA.remove();
        }
    }

    resumeUrlA = document.getElementById(`${rowId}-resume-url-a`);
    if (resumeUrl != "") {
        if (resumeUrl.startsWith("https://")) {
            if (existingRow == null || resumeUrlA == null) {
                resumeUrlA = document.createElement("a");
                resumeUrlA.id = `${rowId}-resume-url-a`;
                resumeUrlA.innerText = "Resume Posting";
                if (cellResumeUrl.innerText != "") {
                    cellResumeUrl.innerText = "";
                }
                cellResumeUrl.appendChild(resumeUrlA);
            }
            resumeUrlA.href = resumeUrl;
        } else {
            cellResumeUrl.innerText = resumeUrl;
        }
    } else {
        if (resumeUrlA == null) {
            cellResumeUrl.innerText = "";
        } else {
            resumeUrlA.remove();
        }
    }

    cellDateCreated.innerText = dateCreated;
    cellViews.innerText = views;
}

async function sendWebRequestWithAuth(url, method, body, headers, attempts) {
    if (url === undefined) {
        throw new Error("url is a required parameter");
    }
    if (method === undefined) {
        throw new Error("method is a required parameter");
    }

    var ret;
    var idToken = common.getCookieByName("id_token");
    var accessToken = common.getCookieByName("access_token");
    var refreshToken = common.getCookieByName("refresh_token");

    if (idToken === undefined || accessToken === undefined || refreshToken === undefined) {
        setStatusElementRedirectToLogin("The authentication tokens appear to be missing.", "to log in again.");
        return null;
    }

    if (headers !== undefined) {
        if (headers.constructor != Object) {
            throw new Error("headers must be of type Object");
        }
    } else {
        headers = {};
    }

    headers["Authorization"] = idToken;
    headers["Content-Type"] = "application/json";

    try {
        var response = await sendWebRequest(url, method, body, headers);
    } catch (e) {
        console.log(`Unable to complete web request. Error: ${e.name} - ${e.message}`);
        return null;
    }

    if (response["status"] == "403") {
        try {
            if (attempts !== undefined && attempts > 0) {
                throw new Error("unknown");
            }
            var { newIdToken, newAccessToken } = await common.renewRefreshToken(refreshToken);
            common.set_token_cookie("id_token", newIdToken);
            common.set_token_cookie("access_token", newAccessToken);
            try {
                ret = sendWebRequestWithAuth(url, method, body, headers, 1);
            } catch (e) {
                console.log(`Error getting resource. Server response: ${ret["body"]}`);
                setStatusElementRedirectToLogin(
                    "There was an unknown error when attempting to authenticate.",
                    `${common.config["managerUrls"]["base"]}${common.config["managerUrls"]["loginRedirect"]}`,
                    "HERE",
                    "to log in again.",
                );
                ret = null;
            }
        } catch (e) {
            if (e.message.includes("refresh_token_invalid")) {
                setStatusElementRedirectToLogin("Unable to verify authentication status.");
                ret = null;
            } else if (e.message.includes("unknown")) {
                setStatusElementRedirectToLogin(
                    "There was an unknown error when attempting to authenticate.",
                    `${common.config["managerUrls"]["base"]}${common.config["managerUrls"]["loginRedirect"]}`,
                    "HERE",
                    "to log in again.",
                );
                ret = null;
            }
        }
    } else if (response["message"] == "unauthorized") {
        setStatusElementRedirectToLogin("There was an unknown error attempting to communicate with the API.", "If this continues to occur, please contact the site administrator.");
        console.log(JSON.stringify(response));
        ret = null;
    } else if (response["status"] < 200 || response["status"] > 499) {
        setStatusElementRedirectToLogin("There was an unknown error attempting to communicate with the API.", "If this continues to occur, please contact the site administrator.");
        console.log(JSON.stringify(response));
        ret = null;
    } else {
        ret = response;
    }
    return ret;
}

async function sendWebRequest(url, method, body, headers) {
    const fetchDict = {};
    const validMethods = ["get", "put", "post", "delete", "options", "head", "connect", "trace", "patch"];

    if (url === undefined) {
        throw new Error("url is a required parameter");
    } else if (url.constructor != String) {
        throw new Error("url must be a string");
    }

    if (method === undefined) {
        throw new Error("method is a required parameter");
    } else if (method.constructor != String) {
        throw new Error("method must be a string");
    } else if (!validMethods.includes(method.toLowerCase())) {
        throw new Error(`method must be one of: ${JSON.stringify(validMethods)}`);
    }
    fetchDict["method"] = method;

    if (headers !== undefined) {
        if (headers.constructor != Object) {
            throw new Error("headers must be an Object");
        } else if (Object.keys(headers).length > 0) {
        }
    } else {
        headers = {};
    }

    fetchDict["headers"] = headers;

    if (body !== undefined) {
        fetchDict["body"] = body;
    }

    fetchDict["credentials"] = "include";
    fetchDict["mode"] = "cors";

    var response;

    try {
        response = await fetch(url, fetchDict);
    } catch (e) {
        throw e;
    }
    const responseContentType = response.headers.get("content-type");
    var retBody;
    if (responseContentType && responseContentType.includes("application/json")) {
        retBody = await response.json();
        if (Object.keys(retBody).includes("message")) {
            retBody = retBody["message"];
        }
    } else {
        retBody = await response.text();
    }

    return { status: response.status, body: retBody };
}

function setStatusElementRedirectToLogin(statusTextBefore, statusTextAfter) {
    if (statusTextAfter !== undefined && statusTextAfter.constructor != String) {
        throw new Error("statusTextAfter must be a string");
    } else if (statusTextAfter === undefined) {
        setStatusElementWithHref(
            `${statusTextBefore} Click`,
            `${common.config["managerUrls"]["base"]}${common.config["managerUrls"]["loginRedirect"]}`,
            "here",
            "to log in again.",
        );
    } else {
        setStatusElementWithHref(
            `${statusTextBefore} Click`,
            `${common.config["managerUrls"]["base"]}${common.config["managerUrls"]["loginRedirect"]}`,
            "here",
            `to log in again. ${statusTextAfter}`,
        );
    }
}

function setStatusElementWithHref(statusTextBefore, href, hrefText, statusTextAfter) {
    var paragraph, h2, textBefore, textAfter, a, aText;

    if (statusTextBefore === undefined) {
        setStatusElement();
    } else if (statusTextBefore.constructor != String) {
        throw new Error("statusTextBefore must be a string");
    } else if (statusTextBefore.constructor != String) {
        throw new Error("statusTextBefore must be a string");
    } else if (statusTextAfter !== undefined && statusTextAfter.constructor != String) {
        throw new Error("statusTextAfter must be a string");
    } else if (href === undefined || href.constructor != String) {
        throw new Error("href must be a string");
    } else if (hrefText === undefined || hrefText.constructor != String) {
        throw new Error("hrefText must be a string");
    } else {
        paragraph = document.createElement("p");
        h2 = document.createElement("h2");
        a = document.createElement("a");
        aText = document.createTextNode(hrefText);
        textBefore = document.createTextNode(statusTextBefore + " ");
        if (statusTextAfter !== undefined) {
            textAfter = document.createTextNode(" " + statusTextAfter + " ");
        }

        paragraph.id = "status-paragraph";
        h2.id = "status-h2";

        a.id = "status-a";

        h2.style.display = "inline";
        a.setAttribute("href", href);

        h2.appendChild(textBefore);
        a.appendChild(aText);
        h2.appendChild(a);
        if (statusTextAfter !== undefined) {
            h2.appendChild(textAfter);
        }
        paragraph.appendChild(h2);
        setStatusElement(paragraph);
    }
}

function setStatusElementText(message) {
    if (message === undefined || message == null || message.constructor != String) {
        throw new Error("message must be a string");
    }

    var h2, textNode;
    h2 = document.createElement("h2");
    textNode = document.createTextNode(message);

    h2.id = "status-h2";
    h2.appendChild(textNode);
    setStatusElement(h2);
}

function setStatusElement(element) {
    var h4, h4Text, h4TextU, paragraph;
    var statusDiv = document.getElementById("status-div");

    function removeStatus(statusElement) {
        const childNodes = statusElement.childNodes;
        const childNodesLength = childNodes.length;
        for (let i = 0; i < childNodesLength; i++) {
            childNodes[0].remove();
        }
    }

    if (element === undefined) {
        removeStatus(statusDiv);
        return;
    }

    if (statusDiv.childNodes.length > 0) {
        removeStatus(statusDiv);
    }

    if (element.childNodes === undefined) {
        throw new Error("element must be a DOM element");
    }

    h4 = document.createElement("h4");
    h4TextU = document.createElement("u");
    h4Text = document.createTextNode("(dismiss)");

    h4.id = "status-h4";
    h4TextU.id = "status-h4-text-u";

    h4.addEventListener("click", () => removeStatus(statusDiv));
    h4.style.color = "blue";
    h4.style.cursor = "pointer";
    h4.style.display = "inline";

    h4TextU.appendChild(h4Text);
    h4.appendChild(h4TextU);
    paragraph = document.createElement("p");
    paragraph.appendChild(h4);
    statusDiv.appendChild(element);
    statusDiv.appendChild(paragraph);
}

function decodeJwtClaims(tokenType) {
    if (tokenType === undefined) {
        tokenType = "id";
    } else if (tokenType.constructor != String) {
        console.log("tokenType must be a string");
        return null;
    } else if (!["access", "id"].includes(tokenType)) {
        console.log("tokenType must be one of: 'access', 'id'");
        return null;
    }

    var token = common.getCookieByName(`${tokenType}_token`);

    if (token === undefined) {
        console.log(`Unable to load token cookie ${tokenType}_token`);
        return null;
    }

    var tokenArray = token.split(".");
    if (tokenArray.length != 3) {
        console.log("Token is invalid");
        return null;
    }
    try {
        var claims = atob(tokenArray[1]);
    } catch (e) {
        console.log("Token is not able to be base64 decoded");
        return null;
    }

    return claims;
}

function setWelcome(displayName) {
    var welcomeText = `Welcome, ${displayName}!`;
    var welcomeDiv = document.getElementById("welcome-div");
    var welcomeH2 = document.getElementById("welcome-h2");
    var welcomeTextNode = document.createTextNode(welcomeText);

    welcomeH2.appendChild(welcomeTextNode);
    welcomeDiv.style.display = "block";
}

function resetDisplay() {
    clearTable();
    clearAddUpdateInputs();

    const elementsDisabledOnLoad = document.querySelectorAll(elementClasses["disableOnLoad"]);
    for (var i = 0; i < elementsDisabledOnLoad.length; i++) {
        elementsDisabledOnLoad[i].disabled = true;
    }

    const links = document.querySelectorAll("link");
    for (var cl in links) {
        var link = links[cl];
        if (link.rel === "stylesheet") {
            link.href.replace(/\?.*|$/, "?" + Date.now());
        }
    }

    const infoParagraph = document.querySelector(elementIds["inputsInfoParagraph"]);
    infoParagraph.innerText = "";

    selectedItem = null;
    isViewActive = true;
}

function selectRow(selectedRowId) {
    const excluded_rows = [getRowId("Refreshing Resumes..."), getRowId("Loading Resumes...")];
    if (selectedRowId !== undefined && Object.keys(selectedRowId).includes(excluded_rows)) {
        return;
    }
    const resumeId = selectedItem;
    if (!modifyingRow) {
        var newSelectedRow = null,
            oldSelectedRow = null;
        if (selectedRowId === undefined) {
            selectedRowId = null;
        }

        if (resumeId != selectedRowId) {
            let buttons;
            // If a new row was selected (and not deselected)
            if (selectedRowId != null) {
                let inResumesObj = Object.keys(resumes[viewType[isViewActive]]).includes(selectedRowId);
                const rowId = getRowId(selectedRowId, true);
                newSelectedRow = document.querySelector(rowId);
                if (!(inResumesObj && newSelectedRow != null)) {
                    console.log(`Resume with id ${selectedRowId} (actual id: ${rowId}) does not exist`);
                    return;
                }
                newSelectedRow.style.backgroundColor = "lightgray";
            }
            // If the selected row is being deselected
            else {
                buttons = document.querySelectorAll(`.${viewType[isViewActive]}-resume-action`);
                for (let i = 0; i < buttons.length; i++) {
                    buttons[i].disabled = true;
                }
                document.querySelector(elementIds["refreshResumeAction"]).disabled = true;
            }

            // If a row was already selected
            if (resumeId != null) {
                oldSelectedRow = document.querySelector(getRowId(resumeId, true));
                if (oldSelectedRow != null) {
                    oldSelectedRow.style.backgroundColor = "white";
                }
            }
            // If a row is being deselected after no rows were selected
            else {
                if (!addingResume) {
                    buttons = document.querySelectorAll(`.${viewType[isViewActive]}-resume-action`);
                    for (let i = 0; i < buttons.length; i++) {
                        buttons[i].disabled = false;
                    }
                }
                document.querySelector(elementIds["refreshResumeAction"]).disabled = false;
            }

            selectedItem = selectedRowId;
        }
    }
}

function getRowId(resumeId, addHashtag) {
    let rowId = "";
    if (addHashtag) {
        rowId = "#";
    }
    rowId = `${rowId}row-${resumeId}`.replaceAll(" ", "_-_").replaceAll(".", "dot");
    return rowId;
}

async function validatePermanentlyDelete(shouldDelete) {
    confirmChoice = shouldDelete;
    document.querySelector(elementIds["confirmDivOverlay"]).hidden = true;
}

async function getChoice(titleText, confirmParagraphText, ...additionalParagraphsText) {
    if (titleText === undefined || titleText.constructor != String) {
        throw new Error("titleText must be a string");
    } else if (confirmParagraphText === undefined || confirmParagraphText.constructor != String) {
        throw new Error("confirmParagraphText must be a string");
    }

    for (let i = 0; i < additionalParagraphsText.length; i++) {
        if (additionalParagraphsText[i] === undefined || additionalParagraphsText[i].constructor != String) {
            throw new Error("all additional paragraphs must be strings");
        }
    }

    var confirmDiv = document.querySelector(elementIds["confirmDivOverlay"]);
    var confirmDivAdditionalParagraphs = document.querySelector(elementIds["confirmDivAdditionalParagraphs"]);
    var confirmTitle = document.querySelector(elementIds["confirmTitle"]);
    var confirmParagraph = document.querySelector(elementIds["confirmParagraph"]);

    confirmTitle.innerText = titleText;
    confirmParagraph.innerText = confirmParagraphText;

    const additionalParagraphNodes = confirmDivAdditionalParagraphs.childNodes;
    const additionalParagraphNodesLength = additionalParagraphNodes.length;

    for (let i = 0; i < additionalParagraphNodesLength; i++) {
        additionalParagraphNodes[0].remove();
    }

    for (let i = 0; i < additionalParagraphsText.length; i++) {
        let paragraph = document.createElement("p");
        paragraph.innerText = additionalParagraphsText[i];
        confirmDivAdditionalParagraphs.appendChild(paragraph);
    }

    confirmDiv.hidden = false;

    while (!confirmDiv.hidden) {
        const sleepTime = 100;
        await new Promise((ret) => setTimeout(ret, sleepTime));
    }

    const choice = confirmChoice;
    confirmChoice = null;
    return choice;
}

window.onload = async function () {
    resetDisplay();
    let decodedJwtClaims = decodeJwtClaims("id");
    try {
        jwtClaims = JSON.parse(decodedJwtClaims);
    } catch (e) {
        console.log("Unable to decode JWT claims");
    }
    if (jwtClaims != null) {
        if (Object.keys(jwtClaims).includes("given_name")) {
            setWelcome(jwtClaims["given_name"]);
        } else if (Object.keys(jwtClaims).includes("email")) {
            setWelcome(jwtClaims["email"]);
        }
    }

    const refreshAllResumesAction = document.querySelector(elementIds["refreshAllResumesAction"]);
    const viewDeletedResumesSelector = document.querySelector(elementIds["viewDeletedResumesSelector"]);
    refreshAllResumesAction.disabled = true;
    viewDeletedResumesSelector.disabled = true;

    document.querySelector(elementIds["overlay"]).addEventListener("click", (e) => {
        if (e.target.tagName == "DIV") {
            selectRow();
        }
    });
    document.querySelector(elementIds["addResumeSelector"]).addEventListener("click", () => changeAddModifyDisplay("add", "show"));
    document.querySelector(elementIds["modifyResumeSelector"]).addEventListener("click", () => changeAddModifyDisplay("modify", "show"));
    document.querySelector(elementIds["deleteResumeAction"]).addEventListener("click", () => deleteOrUndeleteResume());
    document.querySelector(elementIds["cleanupDanglingResumesAction"]).addEventListener("click", () => cleanupDanglingResumes());
    document.querySelector(elementIds["undeleteResumeAction"]).addEventListener("click", () => deleteOrUndeleteResume());
    document.querySelector(elementIds["permanentlyDeleteResumeAction"]).addEventListener("click", () => permanentlyDeleteResume());
    refreshAllResumesAction.addEventListener("click", () => refreshAllResumes());
    document.querySelector(elementIds["refreshResumeAction"]).addEventListener("click", () => refreshResume());
    viewDeletedResumesSelector.addEventListener("click", () => switchResumeView());
    document.querySelector(elementIds["viewActiveResumesSelector"]).addEventListener("click", () => switchResumeView());
    document.querySelector(elementIds["selectResumeFileAction"]).addEventListener("click", () => fileSelector.click());
    document.querySelector(elementIds["clearSelectedResumeFileAction"]).addEventListener("click", () => clearSelectedResumeFile());
    document.querySelector(elementIds["modifyResumeAction"]).addEventListener("click", () => updateResume());
    document.querySelector(elementIds["addResumeAction"]).addEventListener("click", () => addResume());
    document.querySelector(elementIds["cancelAddModifyResumeAction"]).addEventListener("click", () => changeAddModifyDisplay("add", "hide"));
    var inputElements = document.querySelectorAll(elementClasses["addModifyInput"]);
    for (var i = 0; i < inputElements.length; i++) {
        inputElements[i].addEventListener("input", () => checkIfAddUpdateInputsHaveValue());
    }
    fileSelector.addEventListener("change", (element) => loadResumeFile(element));

    document.querySelector(elementIds["confirmYesAction"]).addEventListener("click", () => validatePermanentlyDelete(true));
    document.querySelector(elementIds["confirmNoAction"]).addEventListener("click", () => validatePermanentlyDelete(false));

    await populateTable();

    refreshAllResumesAction.disabled = false;
    viewDeletedResumesSelector.disabled = false;
};
