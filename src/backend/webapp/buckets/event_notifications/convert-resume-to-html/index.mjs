import { readFileSync } from "fs";
import { S3, DeleteObjectCommand, GetObjectCommand, CopyObjectCommand } from "@aws-sdk/client-s3";
import { Upload } from "@aws-sdk/lib-storage";
import { DynamoDBClient, GetItemCommand, UpdateItemCommand } from "@aws-sdk/client-dynamodb";
import { JSDOM } from "jsdom";
import * as mammoth from "mammoth";
import { Buffer } from "node:buffer";
import prettier from "prettier";

var config = JSON.parse(readFileSync(import.meta.dirname + "/config.json").toString("utf8"));

const s3SourceClient = new S3({
    region: config["source_bucket_region"],
});
const s3DestinationClient = new S3({
    region: config["destination_bucket_region"],
});
const dynamodbClient = new DynamoDBClient({
    region: config["dynamodb_table_region"],
});

const resumeTemplateString = readFileSync(import.meta.dirname + "/" + config["html_resume_template"]).toString("utf8");

const workExperienceTemplateString = readFileSync(import.meta.dirname + "/" + config["html_work_experience_template"]).toString("utf8");

export const handler = async (event, context) => {
    const s3ObjectKey = event.Records[0].s3.object.key;
    const s3ObjectFileName = s3ObjectKey.split("/").at(-1);
    const itemId = s3ObjectFileName.split(".").slice(0, -1).join(".");
    const htmlFileName = `${itemId}.html`;
    const s3ObjectVersion = event.Records[0].s3.object.versionId;
    const parsePendingString = "RESUME_PARSE_PENDING";
    const invalidatingCacheString = "CACHE_INVALIDATION_PENDING";
    var errorMessage;
    console.log(`S3 object key: ${s3ObjectKey}`);
    console.log(`S3 object file name: ${s3ObjectFileName}`);
    console.log(`New HTML file name: ${htmlFileName}`);
    console.log(`Item ID: ${itemId}`);
    try {
        try {
            const partitionKey = {
                key: "id",
                type: "S",
                value: itemId,
            };
            const invalidateCache = await getDatabaseInvalidateCacheField(dynamodbClient, config["dynamodb_table_name"], partitionKey, null);
            if (invalidateCache !== undefined || invalidateCache == "") {
                console.log("Cache is still being invalidated");
                throw new Error("handler_cache_still_invalidating_error");
            }
        } catch (e) {
            console.log(`Error getting item from DynamoDB. Stack: ${e.stack}`);
            throw new Error("handler_dynamodb_get_error");
        }
        console.log("Cache for resume is confirmed not being invalidated.");
        console.log("Attempting to set parse pending message");
        try {
            const attributes = [
                {
                    key: "resume_url",
                    type: "S",
                },
            ];
            attributes[0].value = parsePendingString;
            const partitionKey = {
                key: "id",
                type: "S",
                value: itemId,
            };
            await updateDatabaseField(dynamodbClient, config["dynamodb_table_name"], partitionKey, null, attributes);
            console.log(`Added attribute ${attributes[0].key} with value ${attributes[0].value} to DynamoDB table ${config["dynamodb_table_name"]}`);
        } catch (e) {
            console.log(`Error updating DynamoDB item. ${e.stack}`);
            return;
        }
        console.log(`Set DynamoDB key "resume_url" to "${parsePendingString}"`);
        try {
            var eventObjectBody = await getS3ObjectBody(s3SourceClient, config["source_bucket"], s3ObjectKey);
            console.log(`Got object ${s3ObjectKey}'s body from bucket ${config["source_bucket"]}`);
        } catch (e) {
            console.log(`Error getting object from S3. Stack: ${e.stack}`);
            throw new Error("handler_s3_get_object_error");
        }
        try {
            var documentBuffer = new Buffer.from(eventObjectBody);
            console.log("Created buffer from docx file");
        } catch (e) {
            console.log(`Error creating docx buffer. Stack: ${e.stack}`);
            throw new Error("handler_docx_buffer_creation_error");
        }
        try {
            var documentHtml = await mammoth.convertToHtml({ buffer: documentBuffer });
            console.log("Created mammoth HTML string from docx buffer");
        } catch (e) {
            console.log(`Error converting docx to html. Stack: ${e.stack}`);
            throw new Error("handler_docx_conversion_error");
        }
        try {
            var resumeData = extractHTMLData(documentHtml.value);
            console.log("Extracted HTML data from mammoth string");
        } catch (e) {
            console.log(`Error extracting data from the docx file. Stack: ${e.stack}`);
            throw new Error(`extractHTMLData_${e.message}`);
        }
        try {
            var newResume = createResumeHtmlString(resumeTemplateString, workExperienceTemplateString, resumeData);
            console.log("Created new resume's HTML string");
        } catch (e) {
            console.log(`Error creating HTML file string. Stack: ${e.stack}`);
            throw new Error(`createResumeHtmlString_${e.message}`);
        }

        try {
            const destinationKey = `${config["source_bucket_move_destination_folder"]}/${s3ObjectFileName}`;
            await moveS3ObjectWithinBucket(s3SourceClient, config["source_bucket"], s3ObjectKey, destinationKey, s3ObjectVersion);
            console.log(`Moved object ${s3ObjectKey} in bucket ${config["source_bucket"]} to ${destinationKey} in bucket ${config["source_bucket"]}`);
        } catch (e) {
            console.log(`Error moving docx object from source bucket dropoff to archive. ${e.stack}`);
            throw new Error("handler_move_object_error");
        }
        try {
            const destinationKey = `${config["destination_bucket_destination_folder"]}/${htmlFileName}`;
            var prettifiedResume = await prettier.format(newResume, { parser: "html", printWidth: 200 });
            prettifiedResume = prettifiedResume.replace("DOCTYPE html", "doctype html");
            await putS3Object(s3DestinationClient, config["destination_bucket"], destinationKey, prettifiedResume);
            console.log(`Put new resume's HTML string into object ${destinationKey} in bucket ${config["destination_bucket"]}`);
        } catch (e) {
            console.log(`Error putting object from source bucket dropoff to website. ${e.stack}`);
            throw new Error("handler_put_object_error");
        }
    } catch (e) {
        errorMessage = e.message;
    }

    try {
        const attributes = [
            {
                key: "resume_url",
                type: "S",
            },
        ];
        if (errorMessage !== undefined) {
            attributes[0].value = errorMessage;
        } else {
            attributes[0].value = invalidatingCacheString;
            attributes.push({
                key: "invalidate_cache",
                type: "N",
                value: "1",
            });
        }

        const partitionKey = {
            key: "id",
            type: "S",
            value: itemId,
        };
        await updateDatabaseField(dynamodbClient, config["dynamodb_table_name"], partitionKey, null, attributes);
        var updateMessage = `Added attributes ${attributes[0].key} to ${attributes[0].value}`;
        if (errorMessage === undefined) {
            updateMessage = updateMessage.concat(` and ${attributes[1].key} to ${attributes[1].value}`);
        }
        console.log(updateMessage);
    } catch (e) {
        console.log(`Error updating DynamoDB item. ${e.stack}`);
    }
};

function extractHTMLData(htmlString) {
    if (htmlString.constructor != String) {
        throw new Error("html_string_not_string_error");
    }
    const expectedTopLevelTags = {
        0: "table",
        1: "h3",
        2: "table",
        3: "h1",
        4: "table",
        5: "h1",
        6: "table",
        7: "table",
    };
    const resumeObject = {
        contactInfoName: "",
        contactInfoTitle: "",
        contactInfoEmail: "",
        contactInfoPhone: "",
        contactInfoLocation: "",
        professionalSummary: "",
        areasOfExpertise: [],
        keyAccomplishments: [],
        professionalExperience: [],
        education: [],
        certifications: [],
    };
    const sections = {
        introduction: 0,
        areasOfExpertise: 2,
        keyAccomplishments: 4,
        professionalExperience: 6,
        credentials: 7,
    };

    const JSDOMObject = new JSDOM(htmlString);
    const HTMLNode = JSDOMObject.window.document.querySelector("body");
    if (HTMLNode == null) {
        console.log("There appears to be no body in the HTML document.");
        throw new Error("html_body_missing_error");
    }
    const childNodes = HTMLNode.childNodes;
    const childNodeNames = [];
    const expectedNodeNames = Object.values(expectedTopLevelTags);
    for (var i = 0; i < childNodes.length; i++) {
        childNodeNames.push(childNodes[i].tagName.toLowerCase());
    }

    if (childNodes.length != expectedNodeNames.length || JSON.stringify(childNodeNames) != JSON.stringify(expectedNodeNames)) {
        let expected = expectedNodeNames.join(", ");
        let got = childNodeNames.join(", ");
        console.log(`Some top level nodes in resume DOM are missing: expected ${expected} (${childNodes.length} total) - got ${got} (${expectedNodeNames.length} total)`);
        throw new Error("top_level_nodes_missing_error");
    }

    try {
        var introductionJSON = traverseDOMTree(childNodes[sections.introduction]);
    } catch (e) {
        throw new Error(`introduction_${e.message}`);
    }
    try {
        var areasOfExpertiseJSON = traverseDOMTree(childNodes[sections.areasOfExpertise]);
    } catch (e) {
        throw new Error(`areasOfExpertise{e.message}`);
    }
    try {
        var keyAccomplishmentsJSON = traverseDOMTree(childNodes[sections.keyAccomplishments]);
    } catch (e) {
        throw new Error(`keyAccomplishments_${e.message}`);
    }
    try {
        var professionalExperienceJSON = traverseDOMTree(childNodes[sections.professionalExperience]);
    } catch (e) {
        throw new Error(`professionalExperience_${e.message}`);
    }
    try {
        var credentialsJSON = traverseDOMTree(childNodes[sections.credentials]);
    } catch (e) {
        throw new Error(`credentials_${e.message}`);
    }

    try {
        resumeObject.contactInfoName = introductionJSON.rows[0].cells[0].text[0];
        resumeObject.contactInfoTitle = introductionJSON.rows[0].cells[1].text[0];
        resumeObject.contactInfoEmail = introductionJSON.rows[1].cells[0].text[0];
        resumeObject.contactInfoPhone = introductionJSON.rows[2].cells[0].text[0];
        resumeObject.contactInfoLocation = introductionJSON.rows[2].cells[1].text[0];
        resumeObject.professionalSummary = introductionJSON.rows[3].cells[0].text[0];

        let areasOfExpertiseLists = areasOfExpertiseJSON.rows[0].cells;
        for (var i = 0; i < areasOfExpertiseLists.length; i++) {
            for (var j = 0; j < areasOfExpertiseLists[i].list.length; j++) {
                // Translate columns to rows
                resumeObject.areasOfExpertise[i + j * 3] = areasOfExpertiseLists[i].list[j];
            }
        }

        resumeObject.keyAccomplishments = keyAccomplishmentsJSON.rows[0].cells[0].list;

        let professionalExperience = professionalExperienceJSON.rows;
        for (var i = 0; i < professionalExperience.length; i++) {
            let experience = {
                company: professionalExperience[i].cells[0].rows[0].cells[0].text[0].split(" • ").slice(0, 2).join(" • "),
                position: professionalExperience[i].cells[0].rows[0].cells[0].text[0].split(" • ").slice(2, 3).join(""),
                dates: professionalExperience[i].cells[0].rows[1].cells[0].text[0],
                list: professionalExperience[i].cells[0].rows[2].cells[0].list,
            };
            resumeObject.professionalExperience.push(experience);
        }

        for (var i = 1; i < credentialsJSON.rows.length; i += 2) {
            if (
                Object.keys(credentialsJSON.rows[i]).includes("cells") &&
                credentialsJSON.rows[i].cells.length > 0 &&
                Object.keys(credentialsJSON.rows[i + 1]).includes("cells") &&
                credentialsJSON.rows[i + 1].cells.length > 0 &&
                Object.keys(credentialsJSON.rows[i].cells[0]).includes("text")
            ) {
                let education = {
                    degree: credentialsJSON.rows[i].cells[0].text[0],
                    institution: credentialsJSON.rows[i + 1].cells[0].text[0].split(" • ")[0],
                    date: credentialsJSON.rows[i + 1].cells[0].text[0].split(" • ")[1],
                };
                resumeObject.education.push(education);
            }
        }

        for (var i = 1; i < credentialsJSON.rows.length; i += 2) {
            if (
                Object.keys(credentialsJSON.rows[i]).includes("cells") &&
                credentialsJSON.rows[i].cells.length > 1 &&
                Object.keys(credentialsJSON.rows[i + 1]).includes("cells") &&
                credentialsJSON.rows[i + 1].cells.length > 1 &&
                Object.keys(credentialsJSON.rows[i].cells[1]).includes("text")
            ) {
                let certification = {
                    name: credentialsJSON.rows[i].cells[1].text[0],
                    organization: credentialsJSON.rows[i + 1].cells[1].text[0].split(" • ")[0],
                    date: credentialsJSON.rows[i + 1].cells[1].text[0].split(" • ")[1],
                };
                resumeObject.certifications.push(certification);
            }
        }
    } catch (e) {
        console.log(`Error compiling resume from JSON. Stack: ${e.stack} `);
        throw new Error(`resume_format_error`);
    }

    return resumeObject;
}

function createResumeHtmlString(resumeTemplateString, workExperienceTemplateString, resumeData) {
    const sectionIdsAndClasses = {
        contactInfoName: "contact-info-name",
        contactInfoTitle: "contact-info-title",
        contactInfoLocation: "contact-info-location",
        contactInfoEmail: "contact-info-email",
        contactInfoPhone: "contact-info-phone",
        professionalSummary: "professional-summary-paragraph",
        areasOfExpertiseSection: "areas-of-expertise-section",
        keyAccomplishments: "key-accomplishments-section",
        professionalExperienceSection: "professional-experience-section",
        professionalExperienceCompany: "professional-experience-company-",
        professionalExperienceDate: "professional-experience-date-",
        professionalExperienceList: "professional-experience-list-",
        educationList: "education-list",
        certificationsList: "certification-list",
    };

    if (resumeTemplateString === undefined) {
        throw new Error("resumeTemplateString must be defined");
    } else if (workExperienceTemplateString === undefined) {
        throw new Error("workExperienceTemplateString must be defined");
    } else if (resumeData === undefined) {
        throw new Error("resumeData must be defined");
    } else if (resumeTemplateString.constructor != String) {
        throw new Error("resumeTemplateJSDOM must be a JSDOM object");
    }

    const resumeTemplateJSDOM = new JSDOM(resumeTemplateString);
    const workExperienceTemplateJSDOM = new JSDOM(workExperienceTemplateString);

    const resumeDataRequiredKeys = {
        contactInfoName: String,
        contactInfoTitle: String,
        contactInfoLocation: String,
        contactInfoEmail: String,
        contactInfoPhone: String,
        professionalSummary: String,
        areasOfExpertise: Array,
        keyAccomplishments: Array,
        professionalExperience: Array,
        education: Array,
        certifications: Array,
    };

    if (resumeData.constructor != Object) {
        throw new Error("resumeData must be an Object");
    }

    let resumeTemplateDocument = resumeTemplateJSDOM.window.document;
    let workExperienceTemplateDocument = workExperienceTemplateJSDOM.window.document;

    // Get all the static entries
    let staticResumeIds = Object.values(sectionIdsAndClasses).filter(function (id) {
        return !id.endsWith("-");
    });
    let staticWorkExperienceIds = Object.values(sectionIdsAndClasses).filter(function (id) {
        return id.startsWith("work-experience-") && id.endsWith("-");
    });

    let idsMissingFromResumeTemplate = staticResumeIds.filter(function (id) {
        return resumeTemplateDocument.querySelector(`#${id}`) == null;
    });
    let idsMissingFromWorkExperienceTemplate = staticWorkExperienceIds.filter(function (id) {
        return workExperienceTemplateDocument.querySelector(`#${id}`) == null;
    });
    let keysMissingFromResumeData = Object.keys(resumeDataRequiredKeys).filter(function (key) {
        return !Object.keys(resumeData).includes(key);
    });

    if (idsMissingFromResumeTemplate.length > 0) {
        console.log(`Not all IDs are present in the resume template. Missing: ${idsMissingFromResumeTemplate.join(", ")}`);
        throw new Error("resume_template_ids_missing_error");
    }
    if (idsMissingFromWorkExperienceTemplate.length > 0) {
        console.log(`Not all IDs are present in the work experience template. Missing: ${idsMissingFromWorkExperienceTemplate.join(", ")}`);
        throw new Error("work_experience_template_ids_missing_error");
    }
    if (keysMissingFromResumeData.length > 0) {
        console.log(`Not all items are present in the resume data object. Missing: ${keysMissingFromResumeData.join(", ")}`);
        throw new Error("resume_data_items_missing_error");
    }

    let invalidResumeData = Object.keys(resumeData).filter(function (key) {
        return resumeData[key].constructor != resumeDataRequiredKeys[key];
    });

    let flattenedResumeData = flattenObject(resumeData);
    invalidResumeData = invalidResumeData.concat(
        Object.keys(flattenedResumeData).filter(function (key) {
            return flattenedResumeData[key].constructor != String;
        }),
    );

    if (invalidResumeData.length > 0) {
        console.log("The following resume data entries had invalid values: " + invalidResumeData.join(", "));
        throw new Error("resume_date_invalid_entries_error");
    }

    try {
        let contactSectionIdsKeys = Object.keys(sectionIdsAndClasses).filter(function (key) {
            return key.startsWith("contactInfo");
        });

        // Add contact section information
        for (let i = 0; i < contactSectionIdsKeys.length; i++) {
            resumeTemplateDocument.querySelector(`#${sectionIdsAndClasses[contactSectionIdsKeys[i]]}`).innerHTML = resumeData[contactSectionIdsKeys[i]];
        }
    } catch (e) {
        console.log(`Error creating resume contacts section. Stack: ${e.stack}`);
        throw new Error("contact_creation_error");
    }

    try {
        // Add HTML title
        resumeTemplateDocument.querySelector("title").innerHTML = `${resumeData.contactInfoName} Resume`;
    } catch (e) {
        console.log(`Error html title. Stack: ${e.stack}`);
        throw new Error("html_title_creation_error");
    }

    try {
        // Add professional summary information
        resumeTemplateDocument.querySelector(`#${sectionIdsAndClasses.professionalSummary}`).innerHTML = resumeData.professionalSummary;
    } catch (e) {
        console.log(`Error creating resume professional summary. Stack: ${e.stack}`);
        throw new Error("professional_summary_creation_error");
    }

    try {
        // Add areas of expertise rows
        let row;
        for (let i = 0; i < resumeData.areasOfExpertise.length; i++) {
            let rowNum = Math.floor(i / 3);
            if (i % 3 == 0) {
                row = resumeTemplateDocument.createElement("ul");
                row.classList.add("areas-of-expertise-row");
                resumeTemplateDocument.querySelector(`#${sectionIdsAndClasses.areasOfExpertiseSection}`).appendChild(row);
            }
            let item = resumeTemplateDocument.createElement("li");
            item.innerHTML = resumeData.areasOfExpertise[i];
            row.appendChild(item);
        }
    } catch (e) {
        console.log(`Error creating resume areas of expertise section. Stack: ${e.stack}`);
        throw new Error("area_of_experticecreation_error");
    }

    try {
        // Add key accomplishments rows
        for (let i = 0; i < resumeData.keyAccomplishments.length; i++) {
            let item = resumeTemplateDocument.createElement("li");
            item.innerHTML = resumeData.keyAccomplishments[i];
            resumeTemplateDocument.querySelector(`#${sectionIdsAndClasses.keyAccomplishments}`).appendChild(item);
        }
    } catch (e) {
        console.log(`Error creating resume key accomplishments section. Stack: ${e.stack}`);
        throw new Error("key_accomplishments_creation_error");
    }

    try {
        // Add professional experience sections
        for (let i = 0; i < resumeData.professionalExperience.length; i++) {
            let previous = i ? `${i - 1}` : "";
            let company = workExperienceTemplateDocument.querySelector(`#${sectionIdsAndClasses.professionalExperienceCompany}${previous}`);
            company.id = `${sectionIdsAndClasses.professionalExperienceCompany}${i}`;
            company.innerHTML = resumeData.professionalExperience[i].company;
            let date = workExperienceTemplateDocument.querySelector(`#${sectionIdsAndClasses.professionalExperienceDate}${previous}`);
            date.id = `${sectionIdsAndClasses.professionalExperienceDate}${i}`;
            date.innerHTML = resumeData.professionalExperience[i].dates;
            let existingItems = workExperienceTemplateDocument.querySelectorAll(".professional-experience-list-item");
            if (existingItems.length > 0) {
                for (let j = 0; j < existingItems.length; j++) {
                    existingItems[j].remove();
                }
            }
            let list = workExperienceTemplateDocument.querySelector(`#${sectionIdsAndClasses.professionalExperienceList}${previous}`);
            list.id = `${sectionIdsAndClasses.professionalExperienceList}${i}`;
            for (let j = 0; j < resumeData.professionalExperience[i].list.length; j++) {
                let item = workExperienceTemplateDocument.createElement("li");
                item.classList.add("professional-experience-list-item");
                item.innerHTML = resumeData.professionalExperience[i].list[j];
                list.appendChild(item);
            }
            resumeTemplateDocument.querySelector(`#${sectionIdsAndClasses.professionalExperienceSection}`).insertAdjacentHTML("beforeend", workExperienceTemplateJSDOM.serialize());
        }
    } catch (e) {
        console.log(`Error creating resume professional experience section. Stack: ${e.stack}`);
        throw new Error("professional_experience_creation_error");
    }

    try {
        // Add education items
        for (let i = 0; i < resumeData.education.length; i++) {
            let degree = resumeTemplateDocument.createElement("p");
            let degreeStrong = resumeTemplateDocument.createElement("strong");
            let institutionAndDate = resumeTemplateDocument.createElement("p");
            degreeStrong.innerHTML = resumeData.education[i].degree;
            institutionAndDate.innerHTML = resumeData.education[i].institution + " • " + resumeData.education[i].date;
            degree.appendChild(degreeStrong);
            resumeTemplateDocument.querySelector(`#${sectionIdsAndClasses.educationList}`).appendChild(degreeStrong);
            resumeTemplateDocument.querySelector(`#${sectionIdsAndClasses.educationList}`).appendChild(institutionAndDate);
        }
    } catch (e) {
        console.log(`Error creating resume education section. Stack: ${e.stack}`);
        throw new Error("education_creation_error");
    }

    try {
        // Add certifications items
        for (let i = 0; i < resumeData.certifications.length; i++) {
            let name = resumeTemplateDocument.createElement("p");
            let nameStrong = resumeTemplateDocument.createElement("strong");
            let organizationAndDate = resumeTemplateDocument.createElement("p");
            nameStrong.innerHTML = resumeData.certifications[i].name;
            organizationAndDate.innerHTML = resumeData.certifications[i].organization + " • " + resumeData.certifications[i].date;
            nameStrong.appendChild(name);
            resumeTemplateDocument.querySelector(`#${sectionIdsAndClasses.certificationsList}`).appendChild(nameStrong);
            resumeTemplateDocument.querySelector(`#${sectionIdsAndClasses.certificationsList}`).appendChild(organizationAndDate);
        }
    } catch (e) {
        console.log(`Error creating resume certifications section. Stack: ${e.stack}`);
        throw new Error("certifications_creation_error");
    }

    return resumeTemplateJSDOM.serialize();
}

function traverseDOMTree(HTMLNode) {
    try {
        var childNodes = HTMLNode.childNodes;
    } catch (e) {
        console.log(`Error getting node's children. Error: ${e.message}\nStack: ${e.stack}`);
        throw new Error("child_nodes_not_found_error");
    }
    const paragraphTypes = ["P", "H1", "H2", "H3"];
    var tagName = HTMLNode.tagName;
    var ret;
    if (tagName === undefined) {
        ret = "";
    } else if (tagName == "TABLE") {
        ret = { rows: [] };
    } else if (tagName == "TR") {
        ret = { cells: [] };
    } else if (tagName == "UL") {
        ret = { list: [] };
    } else if (paragraphTypes.includes(tagName)) {
        ret = { text: [] };
    } else if (tagName == "TD") {
        ret = [];
    } else {
        ret = [];
    }
    let childNodesLength = childNodes.length;
    try {
        if (childNodesLength > 0) {
            for (var i = 0; i < childNodesLength; i++) {
                let childTagName = childNodes[i].tagName;
                if (childTagName !== undefined && ["STRONG", "I", "U", "BR"].includes(childTagName) && childNodesLength > 1) {
                    continue;
                }
                let response;
                if (childTagName === undefined) {
                    let childConstructorName = childNodes[i].constructor.name;
                    if (childConstructorName == "Text") {
                        response = HTMLNode.innerHTML;
                    }
                } else {
                    response = traverseDOMTree(childNodes[i]);
                }
                if (response.constructor == String) {
                    response = parseText(response);
                }

                // Add response to body element
                if (tagName == "BODY") {
                    ret = ret.concat([response]);
                }
                // Add rows to table
                else if (tagName == "TABLE") {
                    ret["rows"] = ret["rows"].concat(response);
                }
                // Add cells to row
                else if (tagName == "TR") {
                    if (response.length > 0) {
                        ret["cells"] = ret["cells"].concat(response);
                    } else {
                        ret["cells"] = ret["cells"].concat([" "]);
                    }
                }
                // Add list to parent element
                else if (tagName == "UL") {
                    ret["list"] = ret["list"].concat(response);
                }
                // Add text to parent element
                else if (paragraphTypes.includes(tagName)) {
                    ret["text"] = ret["text"].concat(response);
                }
                // If it's any other tag, just pass the response up.
                else {
                    ret = ret.concat(response);
                }
            }
        }
    } catch (e) {
        console.log(`Error traversing the DOM of the file provided. Stack: ${e.stack}`);
        throw new Error("dom_parsing_error");
    }
    return ret;
}

function parseText(text) {
    return text.replace(/(<(?:(?!br).)+?>)/gm, "").split("<br>");
}

function flattenObject(obj) {
    var ret = {};

    for (var i in obj) {
        if (!obj.hasOwnProperty(i)) continue;

        if (typeof obj[i] == "object" && obj[i] !== null) {
            var flatObject = flattenObject(obj[i]);
            for (var x in flatObject) {
                if (!flatObject.hasOwnProperty(x)) continue;

                ret[i + "." + x] = flatObject[x];
            }
        } else {
            ret[i] = obj[i];
        }
    }
    return ret;
}

async function getS3ObjectBody(client, bucket, key) {
    if (client === undefined) {
        throw new Error("Client is a required parameter");
    } else if (client.constructor != S3) {
        throw new Error("Client must be an instance of S3");
    }

    if (bucket === undefined) {
        throw new Error("Bucket is a required parameter");
    } else if (bucket.constructor != String) {
        throw new Error("Bucket must be a string");
    }

    if (key === undefined) {
        throw new Error("key is a required parameter");
    } else if (key.constructor != String) {
        throw new Error("key must be a string");
    }

    const input = {
        Bucket: bucket,
        Key: key,
    };
    const item = await client.send(new GetObjectCommand(input));
    return item.Body.transformToByteArray();
}

async function moveS3ObjectWithinBucket(client, bucket, oldKey, newKey, versionId) {
    if (client === undefined) {
        throw new Error("Client is a required parameter");
    } else if (client.constructor != S3) {
        throw new Error("Client must be an instance of S3");
    }

    if (bucket === undefined) {
        throw new Error("Bucket is a required parameter");
    } else if (bucket.constructor != String) {
        throw new Error("Bucket must be a string");
    }

    if (oldKey === undefined) {
        throw new Error("oldKey is a required parameter");
    } else if (oldKey.constructor != String) {
        throw new Error("oldKey must be a string");
    }

    if (newKey === undefined) {
        throw new Error("newKey is a required parameter");
    } else if (newKey.constructor != String) {
        throw new Error("newKey must be a string");
    }

    const copyInput = {
        Bucket: bucket,
        CopySource: `/${bucket}/${oldKey}`,
        Key: newKey,
    };
    const newItem = await client.send(new CopyObjectCommand(copyInput));

    deleteS3ObjectVersion(client, bucket, oldKey, versionId);

    return newItem.VersionId;
}

async function putS3Object(client, bucket, key, body) {
    if (client === undefined) {
        throw new Error("Client is a required parameter");
    } else if (client.constructor != S3) {
        throw new Error("Client must be an instance of S3");
    }

    if (bucket === undefined) {
        throw new Error("Bucket is a required parameter");
    } else if (bucket.constructor != String) {
        throw new Error("Bucket must be a string");
    }

    if (key === undefined) {
        throw new Error("key is a required parameter");
    } else if (key.constructor != String) {
        throw new Error("key must be a string");
    }

    if (body === undefined) {
        throw new Error("body is a required parameter");
    } else if (body.constructor != String) {
        console.log(body);
        throw new Error("body must be a string");
    }

    const input = {
        Bucket: bucket,
        Key: key,
        Body: new Buffer.from(body),
    };
    const upload = new Upload({
        client: client,
        params: input,
    });
    upload.on("httpUploadProgress", (progress) => {});
    const item = await upload.done();
    return item;
}

async function deleteS3ObjectVersion(client, bucket, key, versionId) {
    const input = {};
    if (client === undefined) {
        throw new Error("Client is a required parameter");
    } else if (client.constructor != S3) {
        throw new Error("Client must be an instance of S3");
    }

    if (bucket === undefined) {
        throw new Error("Bucket is a required parameter");
    } else if (bucket.constructor != String) {
        throw new Error("Bucket must be a string");
    }

    if (key === undefined) {
        throw new Error("key is a required parameter");
    } else if (key.constructor != String) {
        throw new Error("key must be a string");
    }

    if (versionId !== undefined) {
        if (versionId.constructor != String) {
            throw new Error("versionId must be a string");
        } else {
            input.VersionId = versionId;
        }
    }

    input.Bucket = bucket;
    input.Key = key;
    const item = await client.send(new DeleteObjectCommand(input));
    return item;
}

async function getDatabaseInvalidateCacheField(client, table, partitionKey, sortKey) {
    if (client === undefined) {
        throw new Error("Client is a required parameter");
    } else if (client.constructor != DynamoDBClient) {
        throw new Error("Client must be an instance of DynamoDBClient");
    }

    if (table === undefined) {
        throw new Error("table is a required parameter");
    } else if (table.constructor != String) {
        throw new Error("table must be a string");
    }

    var result = validateItem(partitionKey, true);
    if (result != true) {
        throw new Error(`partitionKey${result}`);
    }

    result = validateItem(sortKey);
    if (result != true) {
        throw new Error(`sortKey${result}`);
    }

    const key = {};
    var projectionExpression = "invalidate_cache";
    key[partitionKey.key] = {};
    key[partitionKey.key][partitionKey.type] = partitionKey.value;
    projectionExpression += `,${partitionKey.key}`;

    if (sortKey !== undefined && sortKey != null) {
        key[sortKey.key] = {};
        key[sortKey.key][sortKey.type] = sortKey.value;
        projectionExpression += `,${sortKey.key}`;
    }

    const input = {
        Key: key,
        ProjectionExpression: projectionExpression,
        ConsistentRead: true,
        ReturnConsumedCapacity: "NONE",
        TableName: table,
    };

    const command = new GetItemCommand(input);
    const response = await client.send(command);

    return Object.keys(response.Item).includes("invalidate_cache") ? response.Item.invalidate_cache.N : undefined;
}

async function updateDatabaseField(client, table, partitionKey, sortKey, attributes) {
    if (client === undefined) {
        throw new Error("Client is a required parameter");
    } else if (client.constructor != DynamoDBClient) {
        throw new Error("Client must be an instance of DynamoDBClient");
    }

    if (table === undefined) {
        throw new Error("table is a required parameter");
    } else if (table.constructor != String) {
        throw new Error("table must be a string");
    }

    var result = validateItem(partitionKey, true);
    if (result != true) {
        throw new Error(`partitionKey${result}`);
    }

    result = validateItem(sortKey);
    if (result != true) {
        throw new Error(`sortKey${result}`);
    }

    if (attributes === undefined) {
        throw new Error("attributes is a required parameter");
    } else if (attributes.constructor != Array) {
        throw new Error("attrbutes must be an array");
    }

    const invalidAttributes = [],
        expressionAttributeNames = {},
        expressionAttributeValues = {},
        key = {};
    var updateExpression = "SET ";
    var updateExpressionList = [];
    for (let i = 0; i < attributes.length; i++) {
        result = validateItem(attributes[i]);
        if (result != true) {
            invalidAttributes.push(`attribute ${i}${result}`);
        } else if (invalidAttributes.length == 0) {
            const key = attributes[i].key;
            const value = attributes[i].value;
            const type = attributes[i].type;
            expressionAttributeNames[`#item${i}`] = key;

            expressionAttributeValues[`:item${i}`] = {};
            expressionAttributeValues[`:item${i}`][type] = value;

            updateExpressionList.push(`#item${i} = :item${i}`);
        }
    }
    updateExpression = updateExpression.concat(updateExpressionList.join(", "));
    if (invalidAttributes.length > 0) {
        throw new Error(`The following attributes were invalid.\n- ${invalidAttributes.join("\n -")}`);
    }

    key[partitionKey.key] = {};
    key[partitionKey.key][partitionKey.type] = partitionKey.value;

    if (sortKey !== undefined && sortKey != null) {
        key[sortKey.key] = {};
        key[sortKey.key][sortKey.type] = sortKey.value;
    }

    const input = {
        Key: key,
        ExpressionAttributeNames: expressionAttributeNames,
        ExpressionAttributeValues: expressionAttributeValues,
        ReturnValues: "NONE",
        TableName: table,
        UpdateExpression: updateExpression.trim(),
    };
    await client.send(new UpdateItemCommand(input));
}

function validateItem(item, required) {
    if (item === undefined || item == null) {
        if (required !== undefined && required == true) {
            return " is a required parameter";
        } else {
            return true;
        }
    } else if (item.constructor != Object) {
        return " must be an object";
    }

    const validAttributes = {
        S: String,
        N: String,
        B: Uint8Array,
        SS: Array,
        NS: Array,
        BS: Array,
        L: Array,
        NULL: Boolean,
        BOOL: Boolean,
    };
    const requiredItemFields = ["key", "value", "type"];

    let itemMissingFields = Object.keys(item).filter(function (key) {
        return !requiredItemFields.includes(key);
    });

    if (itemMissingFields.length > 0) {
        return ` must have the keys ${requiredItemFields.join(", ")}. Missing: ${itemMissingFields.join(", ")}`;
    }

    if (item.key.constructor != String) {
        return "'s key must be a string";
    }

    if (!Object.keys(validAttributes).includes(item.type)) {
        return `'s type must be one of ${Object.keys(validAttributes).join(", ")}`;
    }

    if (item.value.constructor != validAttributes[item.type]) {
        return `'s value must be of type ${validAttributes[item.type].name}`;
    }

    return true;
}
