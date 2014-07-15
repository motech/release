#!./env/bin/python2.7
# To run this script you need the following modules installed:
# jenkinsapi==0.2.16
# pytz==2013.9
# requests==2.2.0
# sh==1.09
# wsgiref==0.1.2

import getopt
from jenkinsapi import jenkins
import os
import sh
import shutil
import string
import sys
from xml.dom.minidom import parse

def usage(argv, msg=None):
    if msg:
        print >>sys.stderr, msg
        print >>sys.stderr
    print >>sys.stderr, """\
Usage: %s [options] 

Required options
^^^^^^^^^^^^^^^^
--jenkinsUsername      A user in jenkins who has permission to create new jobs
--jenkinsPassword      The password for the jenkins user
--gerritUsername       A username in gerrit who is in the Bypass Review group.
--version              The version of the release i.e. 0.22
--developmentVersion   The next development version on the branch i.e. 0.22.1-SNAPSHOT
--nextMasterVersion    The next working version on master i.e. 0.23-SNAPSHOT

Optional
^^^^^^^^
--buildDirectory       The base location to check out all source to.  Will delete if it exists
                       defaults to ./builds
--verbose              Be a little chatty on stdout

Standard options
^^^^^^^^^^^^^^^^
-h, --help  show this help and exit
""" % (sys.argv[0])

def main():
    baseBuildDir = "./builds/"
    jenkinsUsername = None
    jenkinsPassword = None
    gerritUsername = None
    version = None
    developmentVersion = None
    nextMasterVersion = None
    branchName = None
    scmTag = None

    verbose = False

    try:
        opts, args = getopt.getopt(sys.argv[1:], 'h',
                                   ['help', 'jenkinsUsername=', 'jenkinsPassword=', 'buildDirectory=', 'developmentVersion=', 'version=', 'nextMasterVersion=', 'gerritUsername=', 'verbose'])
        allopts = set(opt[0] for opt in opts)
        if '-h' in allopts or '--help' in allopts:
            usage(sys.argv)
            return 0

    except getopt.error, err:
        usage(sys.argv, 'Error: %s' % err)
        return 1
    except IndexError:
        usage(sys.argv, 'Error: Insufficient arguments.')
        return 1
    except UnicodeError:
        print >>sys.stderr, (
            'Error: Multibyte filename not supported on this filesystem '
            'encoding (%r).' % fs_encoding)
        return 1

    for opt, val in opts:
        if opt == '--jenkinsUsername':
            jenkinsUsername = val
        elif opt == '--jenkinsPassword':
            jenkinsPassword = val
        elif opt == '--buildDirectory':
            baseBuildDir = val
        elif opt == '--version':
            version = val
            branchName = "{0}.X".format(version)
            scmTag = "release-{0}".format(version)            
        elif opt == '--gerritUsername':
            gerritUsername = val
        elif opt == '--developmentVersion':
            developmentVersion = val
        elif opt == '--nextMasterVersion':
            nextMasterVersion = val
        elif opt == '--verbose':
            verbose = True

    if verbose:
        print "jenkinsUsername: %s" % jenkinsUsername
        print "jenkinsPassword: %s" % jenkinsPassword
        print "baseBuildDir: %s" % baseBuildDir
        print "version: %s" % version
        print "branchName: %s" % branchName
        print "scmTag: %s" % scmTag
        print "gerritUsername: %s" % gerritUsername
        print "developmentVersion: %s" % developmentVersion
        print "nextMasterVersion: %s" % nextMasterVersion

    if not (jenkinsUsername and jenkinsPassword and version and developmentVersion):
        usage(sys.argv)
        return 0

    builds = [
                {
                    'name' : 'Platform-IntegrationTests',
                    'url' : "ssh://{0}@review.motechproject.org:29418/motech".format(gerritUsername),
                    'repository' : 'motech',
                    'jobName' : "Platform-{0}".format(branchName)
                },
                {
                    'name' : 'Modules',
                    'url' : "ssh://{0}@review.motechproject.org:29418/modules".format(gerritUsername),
                    'repository' : 'modules',
                    'jobName' : "Modules-{0}".format(branchName)
                }
            ]

    # Connect to Jenkins
    ci = jenkins.Jenkins("http://ci.motechproject.org", jenkinsUsername, jenkinsPassword)

    # First check if all the builds are passing
    print "Checking build status"
    brokenBuild = False
    for b in builds:
        job = ci.get_job(b['name'])
        build = job.get_last_build()
        isRunning = build.is_running()

        # get_status returns None if building.  is_good returns False if building
        print "\t%s: %s" % (b['name'], build.get_status())

        if not build.is_good():
            print "\t\t*** Unable to continue: failed or in progress build ***"
            brokenBuild = True

    if brokenBuild:
        return 1

    if os.path.exists(baseBuildDir):
        shutil.rmtree(baseBuildDir)

    sh.mkdir(baseBuildDir)
    sh.cd(baseBuildDir)

    print "\nChecking out repositories"
    for b in builds:
        repository = b['repository']
        url = b['url']
        try:
            print "\tCloning " + b['name']
            sh.git("clone", url, repository)
            sh.cd(repository)
            sh.git("config", "remote.origin.push", "refs/heads/*:refs/for/*")
            sh.scp("-p", "-P", "29418", "{0}@review.motechproject.org:hooks/commit-msg".format(gerritUsername), ".git/hooks/")
            sh.cd('..')
        except Exception, e:                
            print "\t\t*** Unable to continue: exception pulling %s ***" % b['repository']
            print e
            return 1

    # run mvn release:branch command
    print "\nBranching repositories"
    for b in builds:
        repository = b['repository']

        print "\tBranching %s" % repository
        sh.cd(repository)

        branchNameArg = "-DbranchName={0}".format(branchName)
        masterVersion = "-DdevelopmentVersion={0}".format(nextMasterVersion)
        scmConnectionArg = "-Dscm.connection=scm:git:ssh://{0}@review.motechproject.org:29418/{1}".format(gerritUsername, repository)
        scmDeveloperConnectinArg = "-Dscm.developerConnection=scm:git:ssh://{0}@review.motechproject.org:29418/{1}".format(gerritUsername, repository)

        try:
            for line in sh.mvn("release:branch", branchNameArg, masterVersion, scmConnectionArg, scmDeveloperConnectinArg, _iter=True):
                if verbose:
                    print(line),            
        except Exception, e:
            print "\t\t*** Unabe to continue: exception branching %s ***" % b['repository']
            print e
            return 1

        sh.cd('..')

    print "\nUpdate MOTECH Version for module repositories"
    for b in builds:
        if b['name'] is "Platform-IntegrationTests":
            continue

        repository = b['repository']

        print "\tUpdateing version for %s" % repository
        sh.cd(repository)

        # Update motech.version to the release version
        pom = open("pom.xml")
        dom = parse(pom)
        pom.close()
        mv = dom.getElementsByTagName('motech.version')
        mv[0].childNodes[0].data = nextMasterVersion

        f = open("pom.xml", 'w')
        dom.writexml(f)
        f.close()

    print "\nCommit and Push changes"
    for b in builds:

        repository = b['repository']

        print "\tUpdateing %s" % repository
        sh.cd(repository)
        # commit the file
        sh.git("commit", "-am", "Update to latest released version")
        sh.git("push", "origin", "master")

        sh.cd('..')

    # Leave the directory with the code folders
    sh.cd('..')

    # Create new jobs on jenkins for each of the builds
    print "\nCreating Jenkins jobs"
    for b in builds:
        repository = b['repository']

            # Use api and config.xml files downloaded from ci.motechproject.org (will need to process config.xml to update version numbers)
            # Also use api to add new jobs to views
            # Should old jobs be deprecated?

        d = { 'branchName' : branchName, 'version' : version, 'developmentVersion' : developmentVersion, 'scmTag' : scmTag}

        filein = open("build-configs/{0}/config.xml".format(repository))
        src = string.Template(filein.read())
        result = src.substitute(d)

        jobName = b['jobName']
        print "\tCreating %s" % jobName
        try:
            newJob = ci.create_job(jobName, result)
            ci.views['Releases'].add_job(jobName, newJob)
        except Exception, e:
            print "\t\t*** Unabe to continue: exception creating job %s ***" % jobName
            print e
            return 1

    return 0

if False:
    # For the modules repositories I need to:
    # - Change motech.version to the release version.  
    # - Trigger the build. 
    # - Change motech.version to the developmentVersion

    # I think to trigger a release build post to:
    # args = "name=releaseVersion&value=%s&name=developmentVersion&value=%s&name=scm.tag&value=%s&json=json:{\"parameter\": [{\"name\": \"releaseVersion\", \"value\": \"%s\"}, {\"name\": \"developmentVersion\", \"value\": \"%s\"}, {\"name\": \"scm.tag\", \"value\": \"%s\"}]}" % ("0.22", "0.22.1-SNAPSHOT", "release-0.22", "0.22", "0.22.1-SNAPSHOT", "release-0.22")
    # curl --data-urlencode args http://ci.motechproject.org/view/Releases/job/Platform-Communications-0.22.X/release/submit
    # releaseVersion, developmentVersion, scm.tag

    # For each of the module before triggering it's release build I'll need to update motech.version in the pom
    # look into http://docs.python.org/2/library/xml.dom.minidom.html for editing the pom file
    for b in builds:
        if b['name'] is "Platform-IntegrationTests":
            continue

        repository = b['repository']

        sh.cd(repository)

        # I'll need to be on the release branch

        # Update motech.version to the release version
        pom = open("pom.xml")
        dom = parse(pom)
        pom.close()
        mv = dom.getElementsByTagName('motech.version')
        mv[0].childNodes[0].data = version

        f = open("pom.xml", 'w')
        dom.writexml(f)
        f.close()

        # commit the file
        # git commit -am "Update motech to latest released version"
        # "git push origin {0}.format(branchName)    
        sh.cd('..')

if __name__ == '__main__':            
    main()

# MOTECH-Platform (Platform-IntegrationTests), Platform-Communications, Platform-Campaigns, Platform-MRS and Platform-Demo jobs are green on our CI server
